"""Brief parse, end to end on the mock provider (playbook §10, Phase 2).

Four claims, each pinned here:

1. **The fixture corpus round-trips.** Every file in ``fixtures/llm/brief-parse/`` is
   an input text plus the exact output the mock provider must produce for it — the
   demo path ("paste a brief, get chips") is asserted byte-for-byte, and the same
   ``expected`` objects are validated against ``BRIEF_PARSE_SCHEMA``, the schema the
   real Anthropic provider is held to. One corpus, two contracts.
2. **Anything not stated → assumption, never silence.** ``stated`` and ``assumptions``
   partition the brief; a field in neither is a failure, not a shrug.
3. **Schema violations are rejected**, both for a live provider response (the
   ``SchemaGate``) and for a corrupt fixture corpus (load-time
   ``FixtureCorpusError``) — a fixture the model would not have been allowed to
   return must never quietly stand in for one.
4. **``POST /brief/parse`` is read-only with respect to the design.** A parse is a
   suggestion; the client applies ``brief.update`` ops after human review. The only
   row the endpoint writes is its ``credit_events(kind='llm')`` metering row.

The corpus/parser tests are file-only and always run; the endpoint tests need the
integration stack and follow the suite's skip-locally/fail-in-CI policy (conftest).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ``services/`` lives at the repo root, which is on PYTHONPATH in CI and in the
# containers but not necessarily when pytest is run bare from apps/api. The API
# resolves the parser with the same tolerance (see routers/projects.py); tests must
# not depend on that tolerance, so pin the path deterministically.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from garh_model.model import ROOM_TYPES  # noqa: E402

from services.common.config import WorkerSettings  # noqa: E402
from services.llm.brief import BriefParser  # noqa: E402
from services.llm.brief_mock import synthesize_brief_parse  # noqa: E402
from services.llm.mock import FixtureCorpusError, MockLlmProvider  # noqa: E402
from services.llm.provider import SchemaGate  # noqa: E402
from services.llm.schemas import BRIEF_PARSE_SCHEMA, ROOM_TYPE_ENUM  # noqa: E402
from services.llm.types import LlmTask, SchemaViolationError  # noqa: E402

CORPUS_DIR = REPO_ROOT / "fixtures" / "llm" / "brief-parse"

#: §10 scalar fields tracked by the assumption machinery (services/llm/brief.py).
TRACKED_SCALARS = (
    "storeys",
    "hasStilt",
    "hasBasement",
    "vastuMode",
    "budgetInr",
    "parkingCount",
    "familySize",
)


def _corpus() -> list[dict[str, Any]]:
    paths = sorted(CORPUS_DIR.glob("brief-parse-*.json"))
    documents = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
        document["__file"] = path.name
        documents.append(document)
    return documents


def _task(text: str) -> LlmTask:
    """The same task shape BriefParser builds — see services/llm/brief.py."""
    return LlmTask(
        name="brief.parse",
        system="",
        user=text,
        schema=BRIEF_PARSE_SCHEMA,
        schema_name="brief_parse",
        fixture_key=text.strip() or "empty",
    )


def _gate() -> SchemaGate:
    return SchemaGate(BRIEF_PARSE_SCHEMA, "brief_parse_test")


# ---------------------------------------------------------------------------
# 1. The corpus round-trips through the mock provider
# ---------------------------------------------------------------------------


def test_corpus_is_big_enough() -> None:
    """The task order says >= 10 realistic briefs. Not "about ten"."""
    assert len(_corpus()) >= 10


def test_every_expected_output_satisfies_the_provider_schema() -> None:
    """The corpus doubles as the real provider's contract tests (one schema, both)."""
    gate = _gate()
    for document in _corpus():
        failures = gate.check(document["expected"])
        assert not failures, (document["__file"], [str(f) for f in failures])


async def test_every_fixture_round_trips_through_the_mock_provider() -> None:
    """Input text in, byte-exact expected output out — through the REAL mock path
    (fixture lookup, synthesis, schema gate), not the parser function alone."""
    provider = MockLlmProvider()
    for document in _corpus():
        result = await provider.complete_json(_task(document["text"]))
        assert result.data == document["expected"], document["__file"]
        assert result.is_mock and result.provider == "mock"


def test_the_parser_is_deterministic() -> None:
    """Same text → same output. No randomness, no time, no dict-order luck."""
    for document in _corpus():
        first = synthesize_brief_parse(document["text"])
        second = synthesize_brief_parse(document["text"])
        assert first == second == document["expected"], document["__file"]


async def test_curated_fixture_keys_still_win_over_synthesis() -> None:
    """An exact corpus hit (services/llm/fixtures/brief-parse.json) stays pinned —
    the golden 'empty' answer must not be replaced by a synthesized parse."""
    provider = MockLlmProvider()
    assert "empty" in provider.known_keys("brief.parse")
    result = await provider.complete_json(_task("   "))
    assert result.data["brief"] == {}, "the curated 'empty' fixture answers blank text"
    assert result.data["unclear"], "empty text asks the architect for a description"


# ---------------------------------------------------------------------------
# 2. Anything not stated → assumption, never silence
# ---------------------------------------------------------------------------


def test_assumptions_are_nonempty_when_text_omits_fields() -> None:
    parsed = synthesize_brief_parse("Make me a house")
    assert parsed["stated"] == []
    fields = {item["field"] for item in parsed["assumptions"]}
    # Every tracked scalar the parser filled in (or deliberately left null) is chipped.
    for name in TRACKED_SCALARS:
        assert "brief.%s" % name in fields, "no assumption chip for %s" % name
    assert all(item["reason"] for item in parsed["assumptions"]), "a chip without a reason"


def test_stated_and_assumptions_partition_every_brief_field() -> None:
    """The product rule, asserted per fixture: nothing falls between the two lists,
    and nothing sits in both."""
    for document in _corpus():
        expected = document["expected"]
        stated = set(expected["stated"])
        assumed = {item["field"] for item in expected["assumptions"]}
        assert not (stated & assumed), (document["__file"], stated & assumed)
        for name in TRACKED_SCALARS:
            path = "brief.%s" % name
            if name in expected["brief"]:
                assert path in stated or path in assumed, (document["__file"], path)
        if expected["brief"].get("rooms"):
            rooms_accounted = "brief.rooms" in stated or any(
                field.startswith("brief.rooms") for field in assumed
            )
            assert rooms_accounted, "%s: rooms neither stated nor assumed" % document["__file"]


def test_room_types_are_model_room_types() -> None:
    """A typo'd room type is a room the solver silently never places."""
    for document in _corpus():
        for room in document["expected"]["brief"].get("rooms", []):
            assert room["type"] in ROOM_TYPES, (document["__file"], room["type"])
            assert isinstance(room["count"], int) and room["count"] >= 0


def test_the_schema_enum_is_generated_from_the_model_contract() -> None:
    """Single source of truth: the LLM schema's room list IS the model's room list."""
    assert tuple(ROOM_TYPE_ENUM) == tuple(ROOM_TYPES)


def test_no_floats_and_no_geometry_in_any_expected_brief() -> None:
    """Money is whole rupees; nothing dimensional beyond optional targetAreaMm2 ints."""
    offenders: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, float):
            offenders.append("%s = %r" % (path, value))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, "%s.%s" % (path, key))
        elif isinstance(value, list):
            for position, item in enumerate(value):
                walk(item, "%s[%d]" % (path, position))

    for document in _corpus():
        walk(document["expected"], document["__file"])
    assert not offenders, offenders


async def test_brief_parser_pipeline_assembles_the_mock_output() -> None:
    """Through BriefParser (the piece the adapter and endpoint actually call)."""
    parser = BriefParser(MockLlmProvider())
    result = await parser.parse("3BHK with pooja room, budget 60 lakh, vastu")
    assert result.brief["vastuMode"] == "advisory"
    assert result.brief["budgetInr"] == 6_000_000
    assert "brief.budgetInr" in result.stated
    assert result.assumptions, "an under-specified brief must carry chips"
    assert 0 <= result.completeness() <= 100


# ---------------------------------------------------------------------------
# 3. Schema violations are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param({"brief": {"storeys": "two"}, "assumptions": []}, id="string-storeys"),
        pytest.param({"brief": {}, "assumptions": [{"field": "x"}]}, id="chip-missing-reason"),
        pytest.param({"brief": {"floorArea": 100}, "assumptions": []}, id="invented-field"),
        pytest.param({"brief": {"budgetInr": 60.5}, "assumptions": []}, id="float-money"),
        pytest.param(
            {"brief": {"rooms": [{"type": "swimming_pool", "count": 1}]}, "assumptions": []},
            id="room-type-outside-the-model-enum",
        ),
        pytest.param({"assumptions": []}, id="missing-brief"),
        pytest.param(["not", "an", "object"], id="not-an-object"),
    ],
)
def test_schema_gate_rejects_a_malformed_provider_response(malformed: Any) -> None:
    gate = _gate()
    assert gate.check(malformed), "gate accepted a malformed response"
    with pytest.raises(SchemaViolationError):
        gate.require(malformed, attempts=1)


async def test_a_malformed_fixture_corpus_fails_loudly_at_load(tmp_path: Path) -> None:
    """A fixture the model would not be allowed to return is an error, not a stand-in."""
    override = tmp_path / "brief-parse.json"
    override.write_text(
        json.dumps(
            {
                "responses": {
                    "bad": {"brief": {"storeys": "two"}, "assumptions": []},
                }
            }
        ),
        encoding="utf-8",
    )
    provider = MockLlmProvider(WorkerSettings(llm_fixture_dir=str(tmp_path)))
    with pytest.raises(FixtureCorpusError):
        await provider.complete_json(_task("anything at all"))


# ---------------------------------------------------------------------------
# 4. The endpoint is read-only w.r.t. the brief (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_parse_endpoint_writes_nothing_but_metering(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """Even ``apply: true`` must not touch the brief, the op log, or anything else —
    the client applies ``brief.update`` after the architect reviews the chips."""
    from garh_api.repositories import BriefRepository, CreditEventRepository

    response = await client.post(
        "%s/projects/%s/brief/parse" % (api, project_a.id),
        json={
            "text": "3BHK, pooja room chahiye, budget 60 lakh, plot 30x40 north facing",
            "apply": True,
        },
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] is False
    assert body["brief"] is None
    assert body["data"], "a parse with no content is not a suggestion"
    assert body["assumptions"], "golden rule 4: the chips are the product"
    assert any(
        "apply" in w.lower() or "applies" in w.lower() for w in body["warnings"]
    ), "ignoring `apply` silently would break the one client that sent it"

    # The brief table is untouched — both over HTTP and straight at the repository.
    over_http = await client.get(
        "%s/projects/%s/brief" % (api, project_a.id), headers=firm_a.headers
    )
    assert over_http.status_code == 200
    assert over_http.json() is None, "the endpoint wrote a brief row"
    stored = await BriefRepository(session, firm_a.ctx()).get_for_project(project_a.id)
    assert stored is None

    # The op log is untouched: a suggestion is not a mutation (golden rule 1).
    branch = await client.get("%s/projects/%s/branch" % (api, project_a.id), headers=firm_a.headers)
    assert branch.json()["headIdx"] == -1, "the endpoint appended ops"

    # The ONE row it must write: the llm metering event, provider named.
    events = await CreditEventRepository(session, firm_a.ctx()).list_recent(kind="llm")
    assert len(events.items) == 1
    event = events.items[0]
    assert event.qty == 1
    assert event.meta["route"] == "brief.parse"
    assert event.meta["provider"] == body["provider"]
    assert event.meta["projectId"] == str(project_a.id)


@pytest.mark.integration
async def test_parse_endpoint_serves_the_deterministic_mock(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """Same text, same answer, zero API keys — the demo path, over HTTP, twice."""
    payload = {"text": "Family of 5, 3 bedrooms, study for wfh, servant room, stilt parking"}
    first = await client.post(
        "%s/projects/%s/brief/parse" % (api, project_a.id),
        json=payload,
        headers=firm_a.headers,
    )
    second = await client.post(
        "%s/projects/%s/brief/parse" % (api, project_a.id),
        json=payload,
        headers=firm_a.headers,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["assumptions"] == second.json()["assumptions"]
    assert first.json()["provider"] == "mock"
    # Everything the text omitted arrived as a chip with a reason, never silently.
    assert all(item["reason"] for item in first.json()["assumptions"])
