"""The copilot, end to end on the mock provider (playbook §10, Phase 6).

Claims pinned here, each traceable to the Phase-6 DoD or §13:

1. **The 40-command eval corpus round-trips.** Every command in
   ``fixtures/llm/copilot-commands/commands.json`` resolves to its expected outcome
   class; >=90% of in-scope commands (in fact all of them) produce *applicable*
   diffs — the ops don't just look right, they ``apply_group`` cleanly on the
   command's model state.
2. **ZERO ops bypass validation.** A proposal only carries ops that passed the
   op-catalog schema, the REAL ``garh_model`` dry-run fold on a fork, and the
   no-new-hard-failure rules diff. Malformed ops never reach the fold; well-formed
   but inapplicable ops never reach the caller; ops riding alongside a refusal are
   dropped; a new hard rules failure is a rejection.
3. **Containment (§13).** PII seeded into every user-authored field the summariser
   actually walks — room name, room notes, storey name, wall name, plot address, and
   the brief — never appears in the prompt, while the summary demonstrably did walk
   those objects (so the test cannot pass vacuously; an earlier version seeded only
   the brief, which ``summarise_model`` never reads, and a real storey-name leak sat
   behind it). The corpus's *tagged* prompt-injection commands land on ``cannotDo``
   with zero ops.
4. **One self-correction round.** An invalid first answer gets exactly one repair
   turn; recovery yields an applicable, ``selfCorrected`` diff; a second failure is
   an honest ``cannotDo``, never partial ops.
5. **§14 budget:** the dry-run fold stays under 10ms.
6. **The route proposes and writes nothing but metering** — apply goes back through
   the op sequencer with the proposal's ``groupId`` (integration tests; skip
   locally, fail in CI per conftest policy).

The corpus tests are file-only and always run; only section 6 needs the stack.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

# ``services/`` lives at the repo root — on PYTHONPATH in CI and the containers, not
# necessarily when pytest runs bare from apps/api. Pin it, same as test_brief_parse.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from garh_model.fold import apply_group, try_fold  # noqa: E402
from garh_model.testing import (  # noqa: E402
    FIXTURE_IDS,
    make_empty_doc,
    make_two_room_plan_with_openings,
)

from services.llm.mock import MockLlmProvider  # noqa: E402
from services.llm.types import LlmResult  # noqa: E402

from garh_api.copilot_loop import (  # noqa: E402
    DRY_RUN_BUDGET_MS,
    ModelFolder,
    NewFailureRulesGate,
    build_copilot_service,
    describe_op,
    run_copilot_command,
)

CORPUS_DIR = REPO_ROOT / "fixtures" / "llm" / "copilot-commands"

#: Phase-6 DoD floor, mirrored from the generator so a hand-edited corpus fails here too.
MIN_COMMANDS = 40
MIN_CANNOT_DO = 8
MIN_CLARIFY = 3
MIN_OP_TYPES = 25
MIN_INJECTION = 2
REQUIRED_SUCCESS_RATE = 0.9


# ---------------------------------------------------------------------------
# Corpus + model states
# ---------------------------------------------------------------------------


def _read(name: str) -> dict[str, Any]:
    with (CORPUS_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _build_state(states_doc: dict[str, Any], name: str) -> Any:
    spec = states_doc["states"][name]
    doc = make_empty_doc() if spec["base"] == "empty" else make_two_room_plan_with_openings()
    for op in spec["ops"]:
        outcome = try_fold(doc, dict(op))
        assert outcome.ok, "model state %r does not fold: %s" % (
            name,
            [issue.message for issue in outcome.issues],
        )
        doc = outcome.model
    return doc


@pytest.fixture(scope="module")
def corpus() -> list[dict[str, Any]]:
    return _read("commands.json")["commands"]


@pytest.fixture(scope="module")
def states() -> dict[str, Any]:
    states_doc = _read("model-states.json")
    return {name: _build_state(states_doc, name) for name in states_doc["states"]}


@pytest.fixture(scope="module")
def mock_provider() -> MockLlmProvider:
    return MockLlmProvider()


class ScriptedProvider:
    """A provider that answers from a script and records every task it was sent.

    For the gate tests: the mock corpus is deliberately all well-behaved, so proving
    that a *misbehaving* provider cannot get ops past validation needs one that
    misbehaves on demand.
    """

    name = "scripted"
    model = "test"

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.tasks: list[Any] = []

    async def complete_json(self, task: Any) -> LlmResult:
        self.tasks.append(task)
        assert self.payloads, "provider called more often than scripted"
        return LlmResult(
            data=json.loads(json.dumps(self.payloads.pop(0))),
            provider=self.name,
            model=self.model,
            is_mock=True,
        )

    async def aclose(self) -> None:
        return None


class SpyFolder(ModelFolder):
    """Counts dry runs, so a test can assert the fold was (not) reached."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def dry_run(self, ops: Any, *, model: Optional[Mapping[str, Any]]) -> Any:
        self.calls += 1
        return super().dry_run(ops, model=model)


def _outcome_of(proposal: Any) -> str:
    if proposal.applicable:
        return "ops"
    if proposal.cannot_do:
        return "cannotDo"
    if proposal.needs_clarification:
        return "needsClarification"
    return "invalid"


# ---------------------------------------------------------------------------
# 1. The eval corpus round-trips (Phase-6 DoD)
# ---------------------------------------------------------------------------


def test_corpus_meets_the_dod_floor(corpus: list[dict[str, Any]]) -> None:
    outcomes = [entry["expected"]["outcome"] for entry in corpus]
    op_types = {
        op_type for entry in corpus for op_type in entry["expected"].get("opTypes", [])
    }
    assert len(corpus) >= MIN_COMMANDS
    assert outcomes.count("cannotDo") >= MIN_CANNOT_DO
    assert outcomes.count("needsClarification") >= MIN_CLARIFY
    assert len(op_types) >= MIN_OP_TYPES, sorted(op_types)
    commands = [entry["command"] for entry in corpus]
    assert len(set(commands)) == len(commands), "the mock keys on command text"
    tagged = [entry for entry in corpus if "injection" in (entry.get("tags") or [])]
    assert len(tagged) >= MIN_INJECTION, "the §13 injection fixtures must stay tagged"
    for entry in tagged:
        assert entry["expected"]["outcome"] == "cannotDo"
        assert not entry["expected"].get("opTypes")


async def test_eval_corpus_end_to_end(
    corpus: list[dict[str, Any]], states: dict[str, Any], mock_provider: MockLlmProvider
) -> None:
    """Every command through the REAL pipeline; >=90% of in-scope commands applicable."""
    mismatches: list[str] = []
    in_scope = 0
    in_scope_ok = 0

    for entry in corpus:
        expected = entry["expected"]
        state = states[entry["modelState"]]
        proposal = await run_copilot_command(
            entry["command"], document=state.to_json(), provider=mock_provider
        )
        got = _outcome_of(proposal)
        if got != expected["outcome"]:
            mismatches.append("%s: expected %s got %s" % (entry["id"], expected["outcome"], got))
            if expected["outcome"] == "ops":
                in_scope += 1
            continue

        if expected["outcome"] != "ops":
            assert proposal.ops == (), "%s: a refusal carried ops" % entry["id"]
            continue

        in_scope += 1
        op_types = [str(op.get("type")) for op in proposal.ops]
        assert op_types == expected["opTypes"], entry["id"]
        # §10: one plain-language line per op, for the diff panel.
        assert len(proposal.plain_language) == len(proposal.ops), entry["id"]
        assert all(line.strip() for line in proposal.plain_language), entry["id"]
        # The diff is APPLICABLE, not just plausible: fold it for real, as one group
        # (exactly what the client's Apply does through the sequencer).
        apply_group(state, [dict(op) for op in proposal.ops])
        in_scope_ok += 1

    assert not mismatches, mismatches
    assert in_scope, "corpus has no in-scope commands"
    rate = in_scope_ok / in_scope
    assert rate >= REQUIRED_SUCCESS_RATE, "in-scope success %.0f%% < %.0f%% (DoD)" % (
        rate * 100,
        REQUIRED_SUCCESS_RATE * 100,
    )


async def test_mock_is_deterministic(
    states: dict[str, Any], mock_provider: MockLlmProvider
) -> None:
    """Same command, same state, same proposal — the demo path is pinned."""
    document = states["two-room"].to_json()
    first = await run_copilot_command(
        "widen the main door to 1200", document=document, provider=mock_provider
    )
    second = await run_copilot_command(
        "widen the main door to 1200", document=document, provider=mock_provider
    )
    assert first.ops == second.ops
    assert first.intent == second.intent


async def test_unknown_command_gets_the_honest_default(
    states: dict[str, Any], mock_provider: MockLlmProvider
) -> None:
    """An unmatched command must exercise the cannotDo path, never invent ops."""
    proposal = await run_copilot_command(
        "recalibrate the flux capacitor in the veranda",
        document=states["two-room"].to_json(),
        provider=mock_provider,
    )
    assert not proposal.applicable
    assert proposal.cannot_do
    assert proposal.ops == ()


async def test_injection_commands_land_on_cannot_do(
    corpus: list[dict[str, Any]], states: dict[str, Any], mock_provider: MockLlmProvider
) -> None:
    """§13: an injection survives at worst as a refusal, never as ops.

    Selected by the generator's explicit ``tags`` marker, not by matching the wording.
    Grepping the prose meant that rewording a fixture — or deleting one — silently
    shrank this test instead of failing it; it was in fact matching only one of the two
    rows at one point. ``generate.py`` guarantees the tag, the outcome and the empty
    op list; this asserts the pipeline honours them.
    """
    injections = [entry for entry in corpus if "injection" in (entry.get("tags") or [])]
    assert len(injections) >= MIN_INJECTION, (
        "the corpus must keep its tagged injection fixtures — see "
        "fixtures/llm/copilot-commands/_tools/generate.py::INJECTION_COMMANDS"
    )
    for entry in injections:
        proposal = await run_copilot_command(
            entry["command"],
            document=states[entry["modelState"]].to_json(),
            provider=mock_provider,
        )
        assert proposal.cannot_do, entry["id"]
        assert proposal.ops == (), entry["id"]


# ---------------------------------------------------------------------------
# 2. Zero ops bypass validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_op",
    [
        pytest.param({"type": "wall.teleport", "payload": {"wallId": "wall_X"}}, id="unknown-type"),
        pytest.param(
            {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 900.5}},
            id="float-mm",
        ),
        pytest.param(
            {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": "900mm"}},
            id="unit-string",
        ),
        pytest.param(
            {"type": "wall.delete", "payload": {"wallId": FIXTURE_IDS["wallSpine"], "cascade": True}},
            id="invented-field",
        ),
        pytest.param({"type": "wall.delete", "payload": {}}, id="missing-required"),
    ],
)
async def test_malformed_ops_never_reach_the_fold(
    states: dict[str, Any], bad_op: dict[str, Any]
) -> None:
    """The op-catalog schema gate runs BEFORE the fold — geometry the LLM invented
    in a shape the taxonomy does not allow is stopped at the door."""
    payload = {"intent": "Do the thing.", "ops": [bad_op]}
    provider = ScriptedProvider([payload, payload])  # initial + the one repair round
    folder = SpyFolder()
    from services.llm.copilot import CopilotService

    service = CopilotService(provider, catalog=folder.catalog, folder=folder, rules=None)
    proposal = await service.propose("do the thing", model=states["two-room"].to_json())
    assert not proposal.applicable
    assert proposal.ops == ()
    assert folder.calls == 0, "a schema-invalid op reached the dry-run fold"
    assert any(issue.code == "OP_SCHEMA_INVALID" for issue in proposal.issues)


async def test_wellformed_but_inapplicable_ops_are_rejected_by_the_real_fold(
    states: dict[str, Any],
) -> None:
    """Schema-valid ops that lie about the document die in the dry run: an opening
    wider than its wall, and a reference to a wall that does not exist."""
    too_wide = {
        "intent": "Widen the door.",
        "ops": [
            {
                "type": "opening.resize",
                "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 50_000},
            }
        ],
    }
    provider = ScriptedProvider([too_wide, too_wide])
    proposal = await run_copilot_command(
        "widen the door to fifty metres",
        document=states["two-room"].to_json(),
        provider=provider,
    )
    assert not proposal.applicable
    assert proposal.ops == ()
    assert proposal.issues, "the fold's machine-readable reasons must surface"
    assert len(provider.tasks) == 2, "exactly one self-correction round (§10)"

    ghost_wall = {
        "intent": "Delete a wall.",
        "ops": [
            # Schema-valid id (Crockford alphabet), but no such wall in the document.
            {"type": "wall.delete", "payload": {"wallId": "wall_01J000000000000000000GH0ST"}}
        ],
    }
    provider = ScriptedProvider([ghost_wall, ghost_wall])
    proposal = await run_copilot_command(
        "delete the ghost wall", document=states["two-room"].to_json(), provider=provider
    )
    assert not proposal.applicable
    assert proposal.ops == ()


async def test_a_new_hard_rules_failure_blocks_the_diff() -> None:
    """The rules gate diffs against the pre-edit baseline: a swap that leaves the
    kitchen without ventilation is rejected — with the rule id as the reason."""
    base = make_two_room_plan_with_openings()
    room_left, room_right = [room.id for room in base.house.rooms]
    # Kitchen + dining assigned, but the dining side has NO window: legal as drawn…
    windowless = apply_group(
        base,
        [
            {"type": "room.assign", "payload": {"roomId": room_left, "type": "kitchen", "name": "Kitchen"}},
            {"type": "room.assign", "payload": {"roomId": room_right, "type": "dining", "name": "Dining"}},
        ],
    ).model
    # …until the copilot swaps the kitchen onto the windowless side.
    swap = {
        "intent": "Swap kitchen and dining.",
        "ops": [
            {"type": "room.assign", "payload": {"roomId": room_left, "type": "dining", "name": "Dining"}},
            {"type": "room.assign", "payload": {"roomId": room_right, "type": "kitchen", "name": "Kitchen"}},
        ],
    }
    provider = ScriptedProvider([swap, swap])
    proposal = await run_copilot_command(
        "swap the kitchen and the dining room",
        document=windowless.to_json(),
        provider=provider,
    )
    assert not proposal.applicable
    assert proposal.ops == ()
    assert any("ventilation" in issue.code for issue in proposal.issues), proposal.issues


async def test_pre_existing_failures_do_not_block_unrelated_edits() -> None:
    """The gate is NEW failures only: on a design that already fails ventilation,
    an unrelated edit still goes through (golden rule 5 — inform, don't block)."""
    base = make_two_room_plan_with_openings()
    room_left, room_right = [room.id for room in base.house.rooms]
    already_failing = apply_group(
        base,
        [
            {"type": "room.assign", "payload": {"roomId": room_left, "type": "dining", "name": "Dining"}},
            {"type": "room.assign", "payload": {"roomId": room_right, "type": "kitchen", "name": "Kitchen"}},
        ],
    ).model
    gate = NewFailureRulesGate(already_failing.to_json())
    assert gate.available
    resize = {
        "intent": "Widen the main door to 1200mm.",
        "ops": [
            {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 1200}}
        ],
    }
    provider = ScriptedProvider([resize])
    proposal = await run_copilot_command(
        "widen the main door to 1200", document=already_failing.to_json(), provider=provider
    )
    assert proposal.applicable, [issue.message for issue in proposal.issues]


async def test_ops_riding_alongside_a_refusal_are_dropped(states: dict[str, Any]) -> None:
    """`cannotDo` + ops is a contradiction; the ops must never survive it (§10)."""
    contradiction = {
        "intent": "Refuse, but also do it.",
        "ops": [
            {"type": "wall.delete", "payload": {"wallId": FIXTURE_IDS["wallSpine"]}}
        ],
        "cannotDo": "I can't do that.",
    }
    provider = ScriptedProvider([contradiction])
    proposal = await run_copilot_command(
        "contradict yourself", document=states["two-room"].to_json(), provider=provider
    )
    assert not proposal.applicable
    assert proposal.ops == ()
    assert proposal.cannot_do


# ---------------------------------------------------------------------------
# 3. Containment: PII never reaches the prompt
# ---------------------------------------------------------------------------


async def test_pii_seeded_into_the_document_never_reaches_the_prompt(
    states: dict[str, Any],
) -> None:
    """§13: "model summaries exclude PII".

    Seeded into every user-authored field that the summariser actually *walks* — not
    only the brief (which ``summarise_model`` never reads, so a brief-only test passes
    whatever the allowlist says). Room name, room notes, storey name and plot address
    are all on the path; each is excluded by the allowlist, not by luck.
    """
    document = states["two-room"].to_json()
    # The brief's `data` is free-form and user-authored — exactly where PII lands
    # when an architect types client details into the brief form.
    document["brief"]["data"] = {
        "clientName": "Ramesh Kumar",
        "notes": "Client Ramesh Kumar, phone +91 9876543210, ramesh.kumar@example.com",
        "phone": "9876543210",
        "storeys": 1,
    }
    # …and into the house/plot fields the summariser really does iterate over.
    document["house"]["rooms"][0]["name"] = "Ramesh Kumar bedroom"
    document["house"]["rooms"][1]["notes"] = "call 9876543210"
    document["house"]["storeys"][0]["name"] = "Ramesh floor 9876543210"
    document["house"]["walls"][0]["name"] = "ramesh.kumar@example.com"
    document.setdefault("plot", {})["address"] = "12 MG Road, Ramesh Kumar"
    ok = {
        "intent": "Widen the main door to 1200mm.",
        "ops": [
            {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 1200}}
        ],
    }
    provider = ScriptedProvider([ok])
    proposal = await run_copilot_command(
        "widen the main door to 1200", document=document, provider=provider
    )
    assert proposal.applicable

    assert provider.tasks, "the provider was never called"
    prompt = "\n".join(task.system + "\n" + task.user for task in provider.tasks)
    for secret in (
        "9876543210",
        "Ramesh",
        "ramesh.kumar@example.com",
        "example.com",
        "MG Road",
    ):
        assert secret not in prompt, "PII %r reached the prompt" % secret
    # Not vacuous: the summary really did walk those objects — the rooms and the
    # storey carrying the seeded names are present, by id, with their real dimensions.
    assert '"rooms"' in prompt and '"storeys"' in prompt
    assert document["house"]["rooms"][0]["id"] in prompt
    assert document["house"]["storeys"][0]["id"] in prompt
    # And the allowlists agree with the module's own idea of what PII looks like.
    from services.llm.redaction import check_allowlists_are_pii_free

    check_allowlists_are_pii_free()
    # And the command itself is fenced as data.
    assert "widen the main door" in prompt


# ---------------------------------------------------------------------------
# 4. Exactly one self-correction round
# ---------------------------------------------------------------------------


async def test_self_correction_recovers_in_one_round(states: dict[str, Any]) -> None:
    bad = {
        "intent": "Widen the door.",
        "ops": [
            {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 50_000}}
        ],
    }
    good = {
        "intent": "Widen the main door to 1200mm.",
        "ops": [
            {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 1200}}
        ],
    }
    provider = ScriptedProvider([bad, good])
    proposal = await run_copilot_command(
        "widen the main door", document=states["two-room"].to_json(), provider=provider
    )
    assert proposal.applicable
    assert proposal.self_corrected
    assert proposal.attempts == 2
    assert [op["payload"]["widthMm"] for op in proposal.ops] == [1200]
    # The repair turn carried the machine-readable reasons back to the model.
    assert "rejected because" in provider.tasks[1].user


async def test_two_failures_end_in_an_honest_refusal(states: dict[str, Any]) -> None:
    bad = {
        "intent": "Widen the door.",
        "ops": [
            {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 50_000}}
        ],
    }
    provider = ScriptedProvider([bad, bad])
    proposal = await run_copilot_command(
        "widen the main door", document=states["two-room"].to_json(), provider=provider
    )
    assert not proposal.applicable
    assert proposal.ops == (), "two failures must never hand back partial ops"
    assert proposal.cannot_do
    assert len(provider.tasks) == 2, "the repair loop is bounded at one round"


# ---------------------------------------------------------------------------
# 5. §14 budget + supporting surfaces
# ---------------------------------------------------------------------------


def test_dry_run_fold_stays_inside_the_10ms_budget(states: dict[str, Any]) -> None:
    """Best-of-three, on the largest in-scope batch — timing tests take the minimum
    because CI noise only ever adds time, never removes it."""
    folder = ModelFolder()
    document = states["kitchen-dining"].to_json()
    room_left, room_right = [room["id"] for room in document["house"]["rooms"]]
    ops = [
        {"type": "room.assign", "payload": {"roomId": room_left, "type": "dining", "name": "Dining"}},
        {"type": "room.assign", "payload": {"roomId": room_right, "type": "kitchen", "name": "Kitchen"}},
    ]
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        outcome = folder.dry_run(ops, model=document)
        best = min(best, (time.perf_counter() - started) * 1000)
        assert outcome.ok
    assert best < DRY_RUN_BUDGET_MS, "dry-run fold took %.2fms (budget %dms)" % (
        best,
        DRY_RUN_BUDGET_MS,
    )


def test_describe_op_speaks_human(states: dict[str, Any]) -> None:
    """§10: plain-language line per op, from OP_CATALOG metadata + document names."""
    state = states["kitchen-dining"]
    room_left = state.house.rooms[0].id
    line = describe_op(
        {"type": "room.assign", "payload": {"roomId": room_left, "type": "dining"}}, state
    )
    assert "Kitchen" in line, line  # the room's current name, from the document
    assert "dining" in line.lower(), line
    resize = describe_op(
        {"type": "opening.resize", "payload": {"openingId": FIXTURE_IDS["doorMain"], "widthMm": 1200}},
        state,
    )
    assert "1200" in resize, resize
    # No raw op-type identifiers in UI copy.
    assert "opening.resize" not in resize


def test_log_record_carries_the_eval_fields(states: dict[str, Any]) -> None:
    """§10: "Log {command, ops, applied|rejected|invalid} for the eval set"."""
    from services.llm.copilot import CopilotProposal

    proposal = CopilotProposal(
        applicable=True,
        intent="Widen the main door to 1200mm.",
        ops=({"type": "opening.resize", "payload": {}},),
    )
    record = proposal.log_record("widen the main door to 1200", "applied")
    assert record["command"] == "widen the main door to 1200"
    assert record["opCount"] == 1
    assert record["opTypes"] == ["opening.resize"]
    assert record["outcome"] == "applied"


def test_rules_gate_degrades_honestly_without_a_plot() -> None:
    """No boundary → rules cannot run → the gate reports nothing and SAYS so."""
    empty = make_empty_doc()
    gate = NewFailureRulesGate(empty.to_json())
    assert gate.available is False
    assert gate.check(empty.to_json()) == ()


# ---------------------------------------------------------------------------
# 6. The route: proposes, meters, writes nothing (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_copilot_route_proposes_then_apply_goes_through_the_sequencer(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """The §13 containment boundary over HTTP: /copilot returns a diff and leaves
    the op log untouched; the client applies through POST /ops with the proposal's
    groupId — the same path as a hand edit."""
    from garh_api.repositories import CreditEventRepository

    response = await client.post(
        "%s/projects/%s/copilot" % (api, project_a.id),
        json={"text": "make the plot 30 by 40 feet"},
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "ops"
    assert body["provider"] == "mock"
    assert body["baseIdx"] == -1
    assert [op["type"] for op in body["ops"]] == ["plot.set_boundary"]
    assert all(op["description"].strip() for op in body["ops"]), "§10: plain language per op"
    assert body["groupId"]

    # Proposing wrote NO ops.
    branch = await client.get(
        "%s/projects/%s/branch" % (api, project_a.id), headers=firm_a.headers
    )
    assert branch.json()["headIdx"] == -1, "the copilot route wrote to the op log"

    # …but it did meter: one credit_events(kind='llm') row, provider named.
    events = await CreditEventRepository(session, firm_a.ctx()).list_recent(kind="llm")
    assert len(events.items) == 1
    meta = events.items[0].meta
    assert meta["route"] == "copilot"
    assert meta["provider"] == "mock"
    assert meta["outcome"] == "ops"
    assert meta["opsCount"] == 1
    assert meta["projectId"] == str(project_a.id)

    # Apply = the CLIENT dispatching the proposal through the ordinary sequencer,
    # as one undo group with copilot provenance.
    applied = await client.post(
        "%s/projects/%s/ops" % (api, project_a.id),
        json={
            "ops": [
                {"type": op["type"], "payload": op["payload"], "clientOpId": "cop-%d" % index}
                for index, op in enumerate(body["ops"])
            ],
            "baseIdx": body["baseIdx"],
            "groupId": body["groupId"],
            "source": "copilot",
        },
        headers=firm_a.headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["headIdx"] == 0


@pytest.mark.integration
async def test_copilot_route_cannot_do_is_metered_and_carries_no_ops(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    from garh_api.repositories import CreditEventRepository

    response = await client.post(
        "%s/projects/%s/copilot" % (api, project_a.id),
        json={"text": "add a swimming pool on the roof"},
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "cannotDo"
    assert body["ops"] == []
    assert body["cannotDo"]

    events = await CreditEventRepository(session, firm_a.ctx()).list_recent(kind="llm")
    assert len(events.items) == 1, "a refusal still spent provider budget; it is metered"
    assert events.items[0].meta["outcome"] == "cannotDo"

    branch = await client.get(
        "%s/projects/%s/branch" % (api, project_a.id), headers=firm_a.headers
    )
    assert branch.json()["headIdx"] == -1


@pytest.mark.integration
async def test_decision_endpoint_logs_and_writes_nothing(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    response = await client.post(
        "%s/projects/%s/copilot/decision" % (api, project_a.id),
        json={"command": "make the plot 30 by 40 feet", "outcome": "applied", "opsCount": 1},
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["logged"] is True

    branch = await client.get(
        "%s/projects/%s/branch" % (api, project_a.id), headers=firm_a.headers
    )
    assert branch.json()["headIdx"] == -1


@pytest.mark.integration
async def test_copilot_route_is_tenant_scoped(
    client: Any, api: str, firm_b: Any, project_a: Any
) -> None:
    """Firm B asking about firm A's project gets a 404, not a proposal (§13)."""
    response = await client.post(
        "%s/projects/%s/copilot" % (api, project_a.id),
        json={"text": "widen the main door to 1200"},
        headers=firm_b.headers,
    )
    assert response.status_code == 404, response.text
