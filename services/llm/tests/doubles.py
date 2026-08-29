"""Provider doubles that misbehave on demand.

The mock provider is deliberately well-behaved, so it cannot demonstrate that a
*hostile* provider gets nothing past the gates. These doubles can: they stream what
they are told to stream, in the order they are told, including orders no honest
provider would give.

They are also the only place a test can watch what actually reached the prompt, which
is what the §13 PII assertions turn on — an assertion about a string the provider never
saw is the vacuous test this repo has already shipped once.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from services.llm.copilot import FoldOutcome, SchemaOnlyFolder
from services.llm.streaming import ProviderDraft
from services.llm.types import LlmResult, LlmTask, LlmUsage


class RecordingProvider:
    """Answers from a script and keeps every task it was handed.

    Has **no** ``stream_json``, so it also exercises the non-streaming fallback inside
    :func:`~services.llm.streaming.guarded_stream`.
    """

    name = "recording"
    model = "test"

    def __init__(self, payloads: Sequence[Mapping[str, Any]]) -> None:
        self.payloads = [dict(payload) for payload in payloads]
        self.tasks: list[LlmTask] = []

    async def complete_json(self, task: LlmTask) -> LlmResult:
        self.tasks.append(task)
        if not self.payloads:
            raise AssertionError("provider called more often than scripted")
        return LlmResult(
            data=json.loads(json.dumps(self.payloads.pop(0))),
            provider=self.name,
            model=self.model,
            usage=LlmUsage(),
            is_mock=True,
        )

    async def aclose(self) -> None:
        return None

    def prompts(self) -> str:
        """Every system + user turn this provider was given, concatenated."""
        return "\n".join(task.system + "\n" + task.user for task in self.tasks)


class ScriptedStreamProvider:
    """Streams a fixed event list per call. Whatever you put in, it yields.

    Used to script the three shapes a stream must refuse (no draft, two drafts, prose
    after the draft) and the one it must never accept (a ready-made ``Answer``).
    """

    name = "scripted-stream"
    model = "test"

    def __init__(self, scripts: Sequence[Sequence[Any]]) -> None:
        self.scripts = [list(script) for script in scripts]
        self.tasks: list[LlmTask] = []

    async def complete_json(self, task: LlmTask) -> LlmResult:
        raise AssertionError("complete_json must not be used when stream_json exists")

    async def stream_json(self, task: LlmTask) -> AsyncIterator[Any]:
        self.tasks.append(task)
        if not self.scripts:
            raise AssertionError("provider streamed more often than scripted")
        for event in self.scripts.pop(0):
            yield event

    async def aclose(self) -> None:
        return None

    def prompts(self) -> str:
        return "\n".join(task.system + "\n" + task.user for task in self.tasks)


def streamed(payload: Mapping[str, Any], *chatter: str) -> list[Any]:
    """A well-formed provider stream: some prose, then one draft."""
    from services.llm.streaming import TextDelta

    events: list[Any] = [TextDelta(text) for text in chatter]
    events.append(ProviderDraft(data=dict(payload), is_mock=True))
    return events


class CountingFolder(SchemaOnlyFolder):
    """A folder that records whether it was reached at all.

    ``calls == 0`` after a rejected proposal is the assertion that a gate ran *in front
    of* the fold rather than after it — the ordering is the containment property, not
    just the verdict.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def dry_run(
        self, ops: Sequence[Mapping[str, Any]], *, model: Mapping[str, Any] | None
    ) -> FoldOutcome:
        self.calls += 1
        return super().dry_run(ops, model=model)


__all__ = [
    "CountingFolder",
    "RecordingProvider",
    "ScriptedStreamProvider",
    "streamed",
]
