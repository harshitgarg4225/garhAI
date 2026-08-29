"""B-4: the copilot streams, and the schema gate still runs before anything folds.

The load-bearing claims, each with a test that goes red when the claim stops holding:

* the gate runs on the **complete** object, and it runs **before** the fold
  (``test_gate_fires_before_the_fold_is_reached``);
* streamed prose is prose — a delta carrying a full ops payload produces zero ops
  (``test_deltas_are_never_read_as_ops``);
* a stream that never delivers a final object is a failure, not an empty success
  (``test_stream_without_a_draft_is_refused``);
* a provider cannot hand over a ready-made :class:`Answer` and skip the gate
  (``test_provider_cannot_forge_an_answer``);
* streaming and blocking produce the *same* proposal, because they are the same
  generator (``test_propose_matches_propose_stream``).
"""

from __future__ import annotations

from typing import Any

import pytest

from services.llm.copilot import CopilotService, ProposalEvent
from services.llm.mock import MockLlmProvider
from services.llm.provider import LlmProvider, gate_for
from services.llm.schemas import COPILOT_SCHEMA
from services.llm.streaming import (
    MAX_PREVIEW_CHARS,
    Answer,
    ProviderDraft,
    StageEvent,
    TextDelta,
    guarded_stream,
    word_chunks,
)
from services.llm.tests.doubles import (
    CountingFolder,
    RecordingProvider,
    ScriptedStreamProvider,
    streamed,
)
from services.llm.types import LlmTask, SchemaViolationError

#: A command the built-in corpus answers with real ops.
CORPUS_COMMAND = "swap the kitchen and the dining room"

#: Structurally invalid: `payload` must be an object. The op-catalog gate would also
#: reject it — the point of using it here is that the RESPONSE schema catches it first,
#: which is what has to be true for streaming not to open a hole.
BAD_PAYLOAD_SHAPE = {
    "intent": "Delete a wall.",
    "ops": [{"type": "wall.delete", "payload": "wall_1"}],
}


def copilot_task(user: str = "widen the door") -> LlmTask:
    return LlmTask(
        name="copilot.ops",
        system="system",
        user=user,
        schema=COPILOT_SCHEMA,
        schema_name="copilot_ops",
        fixture_key=user,
    )


async def drain(provider: LlmProvider, task: LlmTask) -> tuple[list[str], str, Answer | None]:
    """Collect a guarded stream into ``(stages, prose, answer)``."""
    stages: list[str] = []
    prose: list[str] = []
    answer: Answer | None = None
    async for event in guarded_stream(provider, task):
        if isinstance(event, StageEvent):
            stages.append(event.stage)
        elif isinstance(event, TextDelta):
            prose.append(event.text)
        elif isinstance(event, Answer):
            answer = event
    return stages, "".join(prose), answer


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


async def test_gate_fires_on_a_streamed_draft() -> None:
    """A draft that violates the response schema never becomes an Answer."""
    provider = ScriptedStreamProvider([streamed(BAD_PAYLOAD_SHAPE, "Deleting ", "the wall.")])
    with pytest.raises(SchemaViolationError):
        await drain(provider, copilot_task())


async def test_gate_fires_before_the_fold_is_reached() -> None:
    """The ordering, not just the verdict: the fold is never asked to simulate it."""
    folder = CountingFolder()
    provider = ScriptedStreamProvider([streamed(BAD_PAYLOAD_SHAPE, "Deleting the wall.")])
    service = CopilotService(provider, catalog=folder.catalog, folder=folder)

    with pytest.raises(SchemaViolationError):
        await service.propose("delete the spine wall")

    assert folder.calls == 0


async def test_a_valid_stream_still_reaches_the_fold() -> None:
    """The negative control's twin: with a well-formed draft the fold IS reached.

    Without this, ``calls == 0`` above would also pass for a pipeline that folds
    nothing at all — which is exactly the shape of gate this repo has shipped before.
    """
    folder = CountingFolder()
    service = CopilotService(MockLlmProvider(), catalog=folder.catalog, folder=folder)
    proposal = await service.propose(CORPUS_COMMAND)
    assert proposal.applicable
    assert folder.calls == 1


async def test_deltas_are_never_read_as_ops() -> None:
    """Prose that *looks* like an answer is still prose."""
    smuggled = '{"ops":[{"type":"wall.delete","payload":{"wallId":"wall_01"}}]}'
    provider = ScriptedStreamProvider(
        [
            streamed(
                {"intent": "Nothing doing.", "ops": [], "cannotDo": "I can't do that one yet."},
                smuggled,
            )
        ]
    )
    folder = CountingFolder()
    service = CopilotService(provider, catalog=folder.catalog, folder=folder)

    events = [event async for event in service.propose_stream("do something odd")]
    prose = "".join(event.text for event in events if isinstance(event, TextDelta))
    proposals = [event.proposal for event in events if isinstance(event, ProposalEvent)]

    assert prose == smuggled, "the delta must reach the client verbatim, as display text"
    assert len(proposals) == 1
    assert proposals[0].ops == ()
    assert proposals[0].cannot_do
    assert folder.calls == 0


async def test_stream_without_a_draft_is_refused() -> None:
    provider = ScriptedStreamProvider([[TextDelta("thinking…")]])
    with pytest.raises(SchemaViolationError) as caught:
        await drain(provider, copilot_task())
    assert "without a final answer" in caught.value.failures


async def test_two_drafts_are_refused() -> None:
    good = {"intent": "Ask first.", "ops": [], "needsClarification": "Which wall?"}
    provider = ScriptedStreamProvider(
        [[ProviderDraft(data=dict(good)), ProviderDraft(data=dict(good))]]
    )
    with pytest.raises(SchemaViolationError) as caught:
        await drain(provider, copilot_task())
    assert "more than one final answer" in caught.value.failures


async def test_prose_after_the_draft_is_refused() -> None:
    good = {"intent": "Ask first.", "ops": [], "needsClarification": "Which wall?"}
    provider = ScriptedStreamProvider([[ProviderDraft(data=dict(good)), TextDelta("…and also")]])
    with pytest.raises(SchemaViolationError) as caught:
        await drain(provider, copilot_task())
    assert "after its final answer" in caught.value.failures


async def test_provider_cannot_forge_an_answer() -> None:
    """The obvious way to skip the gate: yield the gated type yourself."""
    from services.llm.types import LlmResult

    forged = Answer(
        LlmResult(data=dict(BAD_PAYLOAD_SHAPE), provider="scripted-stream", model="test")
    )
    provider = ScriptedStreamProvider([[forged]])
    with pytest.raises(TypeError) as caught:
        await drain(provider, copilot_task())
    assert "TextDelta or ProviderDraft" in str(caught.value)


async def test_provider_stage_events_are_refused() -> None:
    """Narration is the pipeline's job; two sources of one beat is how it starts lying."""
    good = {"intent": "Ask first.", "ops": [], "needsClarification": "Which wall?"}
    provider = ScriptedStreamProvider([[StageEvent("drafting"), ProviderDraft(data=good)]])
    with pytest.raises(TypeError):
        await drain(provider, copilot_task())


async def test_gated_answer_is_detached_from_the_provider() -> None:
    """A provider that keeps its dict cannot edit what the gate approved."""
    payload: dict[str, Any] = {
        "intent": "Ask first.",
        "ops": [],
        "needsClarification": "Which wall?",
    }
    provider = ScriptedStreamProvider([[ProviderDraft(data=payload)]])
    _stages, _prose, answer = await drain(provider, copilot_task())
    assert answer is not None
    payload["ops"] = [{"type": "wall.delete", "payload": {"wallId": "wall_01"}}]
    assert answer.result.data["ops"] == []


# ---------------------------------------------------------------------------
# The mock streams, so the whole path is testable with no key
# ---------------------------------------------------------------------------


async def test_mock_streams_its_intent_then_the_gated_answer() -> None:
    provider = MockLlmProvider()
    task = copilot_task(CORPUS_COMMAND)
    stages, prose, answer = await drain(provider, task)

    assert answer is not None
    assert answer.result.is_mock
    assert prose == answer.result.data["intent"], "deltas must reconstruct the intent exactly"
    assert prose, "a silent stream is the bug this item exists to fix"
    # Stage narration is the pipeline's, not the provider's: guarded_stream contributes
    # exactly one beat of its own.
    assert stages == ["validating"]


async def test_mock_stream_and_blocking_call_agree() -> None:
    provider = MockLlmProvider()
    task = copilot_task(CORPUS_COMMAND)
    blocking = await provider.complete_json(task)
    _stages, _prose, answer = await drain(provider, task)
    assert answer is not None
    assert answer.result.data == blocking.data


async def test_preview_is_capped() -> None:
    """An unbounded delta stream is a channel flood, not a feature."""
    good = {"intent": "Ask first.", "ops": [], "needsClarification": "Which wall?"}
    flood: list[Any] = [TextDelta("x" * 1_000) for _ in range(20)]
    flood.append(ProviderDraft(data=good))
    provider = ScriptedStreamProvider([flood])
    _stages, prose, answer = await drain(provider, copilot_task())
    assert len(prose) == MAX_PREVIEW_CHARS
    assert answer is not None, "truncating the preview must not lose the answer"


async def test_non_streaming_provider_still_works() -> None:
    """No provider is required to stream; `complete_json` remains a full citizen."""
    payload = {"intent": "Ask first.", "ops": [], "needsClarification": "Which wall?"}
    provider = RecordingProvider([payload])
    assert not hasattr(provider, "stream_json")
    stages, prose, answer = await drain(provider, copilot_task())
    assert answer is not None
    assert answer.result.data == payload
    assert prose == ""
    assert stages == []


# ---------------------------------------------------------------------------
# One implementation, two shapes
# ---------------------------------------------------------------------------


async def test_propose_matches_propose_stream() -> None:
    folder = CountingFolder()
    blocking = await CopilotService(
        MockLlmProvider(), catalog=folder.catalog, folder=folder
    ).propose(CORPUS_COMMAND)

    folder2 = CountingFolder()
    service = CopilotService(MockLlmProvider(), catalog=folder2.catalog, folder=folder2)
    events = [event async for event in service.propose_stream(CORPUS_COMMAND)]
    streamed_proposal = next(event.proposal for event in events if isinstance(event, ProposalEvent))

    assert blocking.to_json() == streamed_proposal.to_json()
    assert blocking.applicable is True


async def test_stage_beats_describe_the_whole_pipeline() -> None:
    folder = CountingFolder()
    service = CopilotService(MockLlmProvider(), catalog=folder.catalog, folder=folder)
    stages = [
        event.stage
        async for event in service.propose_stream(CORPUS_COMMAND)
        if isinstance(event, StageEvent)
    ]
    assert stages == ["drafting", "validating", "folding", "checking-rules", "done"]


async def test_a_stream_always_ends_with_exactly_one_proposal() -> None:
    folder = CountingFolder()
    service = CopilotService(MockLlmProvider(), catalog=folder.catalog, folder=folder)
    for command in (CORPUS_COMMAND, "please compute my structural steel schedule"):
        events = [event async for event in service.propose_stream(command)]
        proposals = [event for event in events if isinstance(event, ProposalEvent)]
        assert len(proposals) == 1
        assert isinstance(events[-1], ProposalEvent)


async def test_unknown_stage_is_rejected() -> None:
    """A closed enum that accepts anything is not a closed enum."""
    with pytest.raises(ValueError):
        StageEvent("thinking-really-hard")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "one", "Widen the main door to 1200mm.", "a  double  spaced  line", "trailing space "],
)
def test_word_chunks_preserve_every_character(text: str) -> None:
    assert "".join(word_chunks(text)) == text


def test_word_chunks_are_deterministic() -> None:
    text = "Swap the kitchen and the dining room."
    assert word_chunks(text) == word_chunks(text)
    assert len(word_chunks(text, words=2)) > len(word_chunks(text, words=4))


def test_gate_cache_is_shared_with_the_blocking_path() -> None:
    """Both paths compile the same schema once, so they cannot diverge on it."""
    task = copilot_task()
    assert gate_for(task) is gate_for(copilot_task("a different command"))
