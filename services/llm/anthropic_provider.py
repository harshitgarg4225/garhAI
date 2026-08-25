"""The real §10 provider: Anthropic Messages API with structured outputs.

Imported lazily by :func:`services.llm.provider.get_llm_provider` — the ``anthropic``
SDK is an optional extra, so a mock-only install never needs it.

Three things here are worth reading before changing:

**1. Structured outputs, not prose parsing.** Every call sets
``output_config.format = {"type": "json_schema", "schema": ...}``, so the model is
constrained to the schema rather than asked nicely for JSON. The response is validated
again on our side anyway (:class:`~services.llm.provider.SchemaGate`) — belt and braces,
because §13's containment rests on it.

**2. The §10 "temperature <= 0.3" rule is satisfied without sending temperature.**
Current Anthropic models (Opus 5, Opus 4.7/4.8, Sonnet 5, Fable 5) **reject**
``temperature``/``top_p``/``top_k`` with a 400 — the parameter was removed. The intent
of the playbook rule is determinism, not the literal field, and structured outputs plus
a low ``effort`` deliver that. :data:`SAMPLING_PARAM_MODELS` lists the older models that
still accept it; for anything else the field is omitted and ``LLM_TEMPERATURE`` is
recorded as advisory only. See ``_supports``.

**3. Refusals are a 200, not an exception.** ``stop_reason == "refusal"`` comes back as
a successful response with empty or partial content. Code that reads ``content[0]``
unconditionally breaks on it, so :meth:`_extract_json` checks ``stop_reason`` first and
raises :class:`~services.llm.types.LlmRefusalError`, which the UI reports honestly
instead of as an outage.

Unsupported-parameter drift is handled defensively: if the API rejects a request naming
an optional parameter we sent, that parameter is dropped and the call retried once, and
the drop is logged loudly. That keeps a model upgrade from taking the copilot down while
still making the mismatch visible.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping

from services.common.config import WorkerSettings, get_worker_settings
from services.common.jsonschema_lite import format_errors
from services.common.logging import get_logger
from services.llm.provider import MAX_SCHEMA_RETRIES, gate_for
from services.llm.types import (
    LlmRefusalError,
    LlmResult,
    LlmTask,
    LlmUnavailableError,
    LlmUsage,
    SchemaViolationError,
)

log = get_logger("llm.anthropic")

#: Model families that still accept `temperature` / `top_p` / `top_k`. Newer models
#: return a 400 for these, so the parameter is sent ONLY for the ones listed here.
#: Matched by prefix. Adding a model here is a deliberate act — check the current API
#: docs first; the default (omit) is the safe side of the fence.
SAMPLING_PARAM_MODELS: tuple[str, ...] = (
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4-0",
    "claude-haiku-4-5",
)

#: Model families that do NOT accept `output_config.effort`. Same prefix matching.
NO_EFFORT_MODELS: tuple[str, ...] = ("claude-sonnet-4-5", "claude-haiku-4-5")

#: Optional request fields we are willing to drop and retry once if rejected.
_DROPPABLE = ("output_config.effort", "temperature", "thinking")


class AnthropicLlmProvider:
    """Structured-output client for the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, settings: WorkerSettings | None = None) -> None:
        self.settings = settings or get_worker_settings()
        self.model = self.settings.anthropic_model
        self._client: Any | None = None
        self._disabled_params: set[str] = set()

    # ------------------------------------------------------------------
    def _sdk(self) -> Any:
        """Build the async SDK client on first use."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - depends on the install extra
                raise LlmUnavailableError(
                    "The assistant is not available on this deployment.",
                    detail=(
                        "PROVIDER_LLM=anthropic requires the `anthropic` package: "
                        'pip install "garh-services[llm]". The default PROVIDER_LLM=mock '
                        "needs no SDK."
                    ),
                ) from exc
            self._client = AsyncAnthropic(
                api_key=self.settings.anthropic_api_key,
                base_url=self.settings.anthropic_base_url or None,
                timeout=float(self.settings.llm_timeout_seconds),
                max_retries=2,
            )
        return self._client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    # ------------------------------------------------------------------
    async def complete_json(self, task: LlmTask) -> LlmResult:
        """Call the model, repairing schema violations at most twice (§10)."""
        started = time.monotonic()
        gate = gate_for(task)
        retries = min(self.settings.llm_schema_retries, MAX_SCHEMA_RETRIES)

        usage = LlmUsage()
        user_turn = task.user
        last_failures = ""
        last_raw: Any = None

        for attempt in range(1, retries + 2):
            raw, call_usage = await self._call(task, user_turn)
            usage = usage.plus(call_usage)
            failures = gate.check(raw)
            if not failures:
                return LlmResult(
                    data=raw,
                    provider=self.name,
                    model=self.model,
                    usage=usage,
                    attempts=attempt,
                    repaired=attempt > 1,
                    is_mock=False,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            last_failures = format_errors(failures)
            last_raw = raw
            log.warning(
                "llm.schema_violation",
                task=task.name,
                attempt=attempt,
                failure_count=len(failures),
            )
            if attempt > retries:
                break
            user_turn = _repair_turn(task.user, last_raw, last_failures)

        raise SchemaViolationError(
            "The assistant could not answer in a usable form.",
            failures=last_failures,
            attempts=retries + 1,
        )

    # ------------------------------------------------------------------
    async def _call(self, task: LlmTask, user_turn: str) -> tuple[Any, LlmUsage]:
        client = self._sdk()
        request = self._build_request(task, user_turn)
        # §13: requests and responses are logged minus PII — sizes, names and token
        # counts only. The prompt carries the client's words and MUST NOT appear in a
        # log line; neither may the response body (it can echo the brief back).
        log.info(
            "llm.anthropic.request",
            task=task.name,
            model=self.model,
            schema=task.schema_name,
            system_chars=len(task.system),
            user_chars=len(user_turn),
            max_tokens=request.get("max_tokens"),
        )
        try:
            response = await client.messages.create(**request)
        except Exception as exc:  # noqa: BLE001 - classified below
            dropped = self._drop_rejected_param(exc, request)
            if dropped:
                log.error(
                    "llm.parameter_rejected",
                    task=task.name,
                    model=self.model,
                    parameter=dropped,
                    hint="this model no longer accepts that parameter; update "
                    "SAMPLING_PARAM_MODELS / NO_EFFORT_MODELS in "
                    "services/llm/anthropic_provider.py",
                )
                response = await client.messages.create(**request)
            else:
                raise _as_llm_error(exc) from exc

        usage = _usage_from(response)
        log.info(
            "llm.anthropic.response",
            task=task.name,
            model=self.model,
            stop_reason=getattr(response, "stop_reason", None),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        return self._extract_json(response), usage

    def _build_request(self, task: LlmTask, user_turn: str) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": min(task.max_output_tokens, self.settings.llm_max_output_tokens),
            "system": task.system,
            "messages": [{"role": "user", "content": user_turn}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "name": task.schema_name,
                    "schema": _strip_unsupported(task.schema),
                }
            },
        }
        if self._supports("output_config.effort") and not _prefix_match(
            self.model, NO_EFFORT_MODELS
        ):
            request["output_config"]["effort"] = task.effort
        if self._supports("temperature") and _prefix_match(self.model, SAMPLING_PARAM_MODELS):
            # §10 caps this at 0.3; WorkerSettings already refuses anything higher.
            request["temperature"] = self.settings.llm_temperature
        return request

    def _supports(self, parameter: str) -> bool:
        return parameter not in self._disabled_params

    def _drop_rejected_param(self, exc: Exception, request: dict[str, Any]) -> str | None:
        """Remove an optional parameter the API rejected, so the retry can succeed."""
        if _status_of(exc) != 400:
            return None
        message = str(exc).lower()
        for parameter in _DROPPABLE:
            leaf = parameter.rsplit(".", 1)[-1]
            if leaf not in message or parameter in self._disabled_params:
                continue
            self._disabled_params.add(parameter)
            if "." in parameter:
                head, _, tail = parameter.partition(".")
                container = request.get(head)
                if isinstance(container, dict):
                    container.pop(tail, None)
            else:
                request.pop(parameter, None)
            return parameter
        return None

    def _extract_json(self, response: Any) -> Any:
        """Pull the JSON object out of a response, checking ``stop_reason`` first."""
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LlmRefusalError(
                "The assistant declined this request.", category=category
            )
        if stop_reason == "max_tokens":
            raise SchemaViolationError(
                "The assistant's answer was cut off before it finished.",
                failures="response hit max_tokens; the JSON is incomplete",
                attempts=1,
            )

        parsed = getattr(response, "parsed_output", None)
        if isinstance(parsed, dict):
            return parsed

        text = "".join(
            str(getattr(block, "text", ""))
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise SchemaViolationError(
                "The assistant returned an empty answer.",
                failures="no text content in the response (stop_reason=%s)" % stop_reason,
                attempts=1,
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaViolationError(
                "The assistant's answer did not come back in a usable shape.",
                failures="response was not valid JSON: %s" % exc,
                attempts=1,
            ) from exc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
#: Keywords the structured-output compiler does not accept. Ours are validated
#: locally anyway, so stripping them costs nothing and avoids a 400.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
     "minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties",
     "uniqueItems", "pattern", "$comment"}
)


def _strip_unsupported(schema: Any) -> Any:
    """Remove constraints the structured-output compiler rejects, recursively.

    The local :class:`SchemaGate` still enforces every one of them on the response, so
    nothing is actually relaxed — this only changes what the *compiler* is asked to
    guarantee.
    """
    if isinstance(schema, dict):
        return {
            key: _strip_unsupported(value)
            for key, value in schema.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYWORDS
        }
    if isinstance(schema, list):
        return [_strip_unsupported(item) for item in schema]
    return schema


def _repair_turn(original_user: str, raw: Any, failures: str) -> str:
    """The self-correction turn used for schema violations (§10: feed reasons back)."""
    try:
        echoed = json.dumps(raw, ensure_ascii=False)[:4_000]
    except (TypeError, ValueError):
        echoed = str(raw)[:4_000]
    return (
        "%s\n\nYour previous answer did not match the required JSON schema.\n"
        "You returned:\n%s\n\nThe problems were:\n%s\n\n"
        "Return the corrected JSON. Change nothing else." % (original_user, echoed, failures)
    )


def _usage_from(response: Any) -> LlmUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return LlmUsage()
    return LlmUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


def _status_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    match = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(match.group(1)) if match else None


def _as_llm_error(exc: Exception) -> Exception:
    """Map an SDK exception onto our taxonomy without leaking internals to the UI."""
    status = _status_of(exc)
    if status == 401 or status == 403:
        return LlmUnavailableError(
            "The assistant is not configured correctly on this deployment.",
            detail="auth failed (HTTP %s): %s" % (status, exc),
        )
    if status == 429:
        return LlmUnavailableError(
            "The assistant is busy right now.", detail="rate limited: %s" % exc
        )
    if status is not None and 400 <= status < 500:
        return LlmUnavailableError(
            "The assistant could not process that request.",
            detail="HTTP %s: %s" % (status, exc),
        )
    return LlmUnavailableError(
        "The assistant is temporarily unavailable.", detail=str(exc)
    )


def describe_model_support(model: str) -> Mapping[str, bool]:
    """What this provider will send for ``model``. Used by the boot log and tests."""
    return {
        "temperature": _prefix_match(model, SAMPLING_PARAM_MODELS),
        "effort": not _prefix_match(model, NO_EFFORT_MODELS),
    }


def _prefix_match(model: str, prefixes: tuple[str, ...]) -> bool:
    return any(model.startswith(prefix) for prefix in prefixes)


__all__ = [
    "NO_EFFORT_MODELS",
    "SAMPLING_PARAM_MODELS",
    "AnthropicLlmProvider",
    "describe_model_support",
]
