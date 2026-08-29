"""Streaming for the §10 LLM layer — visible progress with the gate still shut (B-4).

The copilot used to return one blob after several silent seconds, which reads as
broken. This module makes the wait legible without opening a second door into the op
pipeline.

The contract, and why it is shaped this way
-------------------------------------------
A **provider** stream carries exactly two kinds of event, and only one of them can
ever become ops:

* :class:`TextDelta` — a fragment of the model's **prose**. Advisory, display-only.
  It is never parsed, never folded, never turned into anything the model core sees.
  ``TextDelta`` has one field and it is a ``str``: there is no shape in which a delta
  could smuggle an op past a gate, because nothing downstream reads deltas as data.
* :class:`ProviderDraft` — the terminal event, carrying the complete but
  **unvalidated** object.

:class:`StageEvent` — where the pipeline is ("drafting", "folding", …) — is narration
the *pipeline* emits, never the provider. Keeping it out of the provider contract
avoids two sources of the same beat, which is how a progress bar starts lying.

:func:`guarded_stream` is the only thing that turns a ``ProviderDraft`` into an
:class:`Answer`, and it does so by running the task's :class:`~services.llm.provider.
SchemaGate` over the whole object first. So the §13 property the copilot's containment
rests on — *the schema gate runs on the complete object before anything is folded* —
holds under streaming for the same reason it holds without it: there is exactly one
constructor of a validated result, and it validates.

Three failure modes are refused rather than tolerated, because each of them is a way a
gate could end up silently not firing:

1. a provider that yields anything other than the two event types above — including
   a pre-built :class:`Answer`, which is the obvious way to try to skip the gate;
2. a provider that ends its stream without a ``ProviderDraft`` (no answer at all);
3. a provider that sends a second ``ProviderDraft``, or prose after its draft.

Providers that cannot stream need no changes. :func:`guarded_stream` falls back to
``complete_json``, whose own contract already says the provider validates before
returning — so both paths hand the caller a result that has been through the gate
exactly once, and the copilot has one code path either way. **Both paths emit the same
stage beats**, including ``"validating"``: the five-beat walk is the pipeline's promise
to the client, and a progress bar that silently loses a beat because the configured
provider happens not to implement ``stream_json`` is a progress bar that lies about
which gates ran.

A provider that *does* implement ``stream_json`` must satisfy
:class:`StreamingLlmProvider` in full, checked here rather than assumed. Half a
streaming provider would otherwise take the streaming path and fail somewhere further
in — and a protocol nothing checks is documentation, not a contract.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from services.common.logging import get_logger
from services.llm.provider import LlmProvider, gate_for
from services.llm.types import LlmResult, LlmTask, LlmUsage, SchemaViolationError

log = get_logger("llm.streaming")

#: Hard cap on streamed prose per call. The authoritative answer is a separate event,
#: so truncating the preview costs nothing — while an unbounded delta stream would let
#: a provider (or an injection that talked one into it) flood the SSE channel.
MAX_PREVIEW_CHARS = 8_000

#: Where the pipeline is. A closed list because the client renders one label per value;
#: an unknown stage would render as nothing at all.
Stage = Literal["drafting", "revising", "validating", "folding", "checking-rules", "done"]

STAGES: tuple[Stage, ...] = (
    "drafting",
    "revising",
    "validating",
    "folding",
    "checking-rules",
    "done",
)


@dataclass(frozen=True)
class StageEvent:
    """A progress beat, emitted by the pipeline. Carries no model output."""

    stage: Stage

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(
                "unknown stream stage %r. Expected one of: %s." % (self.stage, ", ".join(STAGES))
            )

    def to_json(self) -> dict[str, Any]:
        return {"type": "stage", "stage": self.stage}


@dataclass(frozen=True)
class TextDelta:
    """A fragment of the model's prose, for display only.

    Deliberately one string field. Nothing downstream parses it, so a delta cannot
    become an op however it is spelled — the structured answer travels separately and
    is gated separately.
    """

    text: str

    def to_json(self) -> dict[str, Any]:
        return {"type": "delta", "text": self.text}


@dataclass(frozen=True)
class ProviderDraft:
    """A provider's terminal event: the complete answer, **not yet validated**.

    Named for what it is. A provider hands over a draft; only :func:`guarded_stream`
    can promote one to an :class:`Answer`, and only by passing the schema gate.
    """

    data: Any
    usage: LlmUsage = field(default_factory=LlmUsage)
    attempts: int = 1
    is_mock: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class Answer:
    """Terminal event of a *gated* stream: a schema-valid :class:`LlmResult`."""

    result: LlmResult

    def to_json(self) -> dict[str, Any]:
        return {"type": "answer", **self.result.summary()}


@runtime_checkable
class StreamingLlmProvider(Protocol):
    """A provider that can also stream.

    Deliberately a **second** protocol rather than a widening of
    :class:`~services.llm.provider.LlmProvider`: that one is ``runtime_checkable`` and
    already implemented by objects in tests and scripts, and adding a method to it
    would silently reclassify every one of them.
    """

    name: str
    model: str

    def stream_json(self, task: LlmTask) -> AsyncIterator[Any]:
        """Yield :class:`TextDelta` prose, then exactly one :class:`ProviderDraft`."""
        ...

    async def complete_json(self, task: LlmTask) -> LlmResult: ...

    async def aclose(self) -> None: ...


#: Every member :class:`StreamingLlmProvider` requires, read off the protocol itself
#: rather than restated beside it — a hand-copied list drifts, and the drifted half is
#: always the half doing the checking.
STREAMING_PROVIDER_MEMBERS: tuple[str, ...] = tuple(
    sorted(
        {name for name in vars(StreamingLlmProvider) if not name.startswith("_")}
        | set(StreamingLlmProvider.__annotations__)
    )
)


async def guarded_stream(
    provider: LlmProvider,
    task: LlmTask,
    *,
    max_preview_chars: int = MAX_PREVIEW_CHARS,
) -> AsyncIterator[Any]:
    """Stream a task, ending in an :class:`Answer` that has passed the schema gate.

    The gate is not optional and not delegated: even a provider whose own
    ``complete_json`` validates gets re-checked here when it streams, because the
    streaming path is new surface and "the provider promised" is not a gate.
    """
    stream_json = getattr(provider, "stream_json", None)
    if stream_json is None:
        # Non-streaming provider. Its `complete_json` contract is "validate before
        # returning" (see LlmProvider), so the object arriving here has already been
        # through this task's gate exactly once.
        result = await provider.complete_json(task)
        # The same beat, in the same position, as the streaming branch below. The stage
        # contract belongs to the pipeline, so it must not depend on which provider is
        # configured: `AnthropicLlmProvider` has no `stream_json`, and a five-beat walk
        # that becomes four on the real provider is the shape of contract that gets
        # written into a client and then quietly broken in production.
        yield StageEvent("validating")
        yield Answer(result)
        return

    if not isinstance(provider, StreamingLlmProvider):
        missing = [name for name in STREAMING_PROVIDER_MEMBERS if not hasattr(provider, name)]
        raise TypeError(
            "%s offers stream_json but does not satisfy StreamingLlmProvider%s. A "
            "half-implemented streaming provider takes the streaming path and fails "
            "later, somewhere with less context than here."
            % (type(provider).__name__, (": missing %s" % ", ".join(missing)) if missing else "")
        )

    gate = gate_for(task)
    draft: ProviderDraft | None = None
    preview_chars = 0

    async for event in stream_json(task):
        if isinstance(event, TextDelta):
            if draft is not None:
                raise SchemaViolationError(
                    "The assistant's answer did not come back in a usable shape.",
                    failures="provider streamed prose after its final answer",
                    attempts=1,
                )
            if not isinstance(event.text, str):
                raise TypeError("TextDelta.text must be a str, got %r" % type(event.text).__name__)
            remaining = max_preview_chars - preview_chars
            if remaining <= 0:
                continue
            text = event.text[:remaining]
            preview_chars += len(text)
            if text:
                yield TextDelta(text)
        elif isinstance(event, ProviderDraft):
            if draft is not None:
                raise SchemaViolationError(
                    "The assistant's answer did not come back in a usable shape.",
                    failures="provider sent more than one final answer",
                    attempts=1,
                )
            draft = event
        else:
            # Includes a provider trying to hand over a ready-made `Answer`, which is
            # the one shape that would skip the gate below.
            raise TypeError(
                "a provider stream may only yield TextDelta or ProviderDraft; got %r. "
                "Stage narration belongs to the pipeline, not the provider." % type(event).__name__
            )

    if draft is None:
        raise SchemaViolationError(
            "The assistant's answer did not come back in a usable shape.",
            failures="the stream ended without a final answer",
            attempts=1,
        )

    yield StageEvent("validating")
    data = gate.require(draft.data, attempts=draft.attempts)
    log.info(
        "llm.stream.gated",
        task=task.name,
        schema=task.schema_name,
        preview_chars=preview_chars,
        provider=provider.name,
    )
    yield Answer(
        LlmResult(
            data=_detached(data),
            provider=provider.name,
            model=provider.model,
            usage=draft.usage,
            attempts=draft.attempts,
            repaired=False,
            is_mock=draft.is_mock,
            duration_ms=draft.duration_ms,
        )
    )


def word_chunks(text: str, *, words: int = 3) -> list[str]:
    """Split prose into delta-sized pieces, preserving every character.

    ``"".join(word_chunks(t)) == t`` for any ``t`` — a preview that silently dropped a
    space would look like a rendering bug in the copilot panel. Deterministic, so a
    mock stream is byte-reproducible in CI.
    """
    if words < 1:
        raise ValueError("words must be >= 1")
    pieces = text.split(" ")
    chunks: list[str] = []
    for start in range(0, len(pieces), words):
        group = pieces[start : start + words]
        chunk = " ".join(group)
        if start + words < len(pieces):
            chunk += " "
        if chunk:
            chunks.append(chunk)
    return chunks


def _detached(data: dict[str, Any]) -> dict[str, Any]:
    """A copy the provider cannot still hold a reference to.

    The gate validated *this* object; a provider that kept the reference could mutate
    it afterwards and the fold would see something the gate never saw.
    """
    try:
        detached = json.loads(json.dumps(data))
    except (TypeError, ValueError) as exc:
        raise SchemaViolationError(
            "The assistant's answer did not come back in a usable shape.",
            failures="the answer was not JSON-representable: %s" % exc,
            attempts=1,
        ) from exc
    if not isinstance(detached, dict):  # pragma: no cover - gate.require guarantees dict
        raise SchemaViolationError(
            "The assistant's answer did not come back in a usable shape.",
            failures="top level must be a JSON object",
            attempts=1,
        )
    return detached


__all__ = [
    "MAX_PREVIEW_CHARS",
    "STAGES",
    "STREAMING_PROVIDER_MEMBERS",
    "Answer",
    "ProviderDraft",
    "Stage",
    "StageEvent",
    "StreamingLlmProvider",
    "TextDelta",
    "guarded_stream",
    "word_chunks",
]
