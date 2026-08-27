"""The fixture-driven mock LLM provider — the default everywhere (§10).

This is the provider that makes the locked decision true: *the full app runs and is
e2e-testable with zero API keys*. It is not a stub. It:

* loads a corpus of ``{fixtureKey: response}`` files, built-in ones first and then any
  ``LLM_FIXTURE_DIR`` overrides;
* **validates every fixture against the same JSON Schema the real provider is held
  to**, at load time. A fixture the model would not have been allowed to return is a
  loud error, not a convenient shortcut. This is what keeps the mock honest enough for
  the e2e suite to mean something;
* resolves a key deterministically — exact match, then normalised match, then a
  keyword-scored best match, then a task-specific default — so the same command always
  produces the same answer on every machine and in every CI run.

The fallback for an unmatched copilot command is deliberately ``cannotDo``. A mock that
invented plausible ops would let a broken prompt pass CI; one that admits it does not
know the command exercises the real "can't do that yet" UI path instead.

**``brief.parse`` is the exception to the fixture-only rule.** A brief is free prose;
a canned answer to unseen prose would make the zero-keys demo a lie. So an exact or
normalised fixture hit still wins (curated corpora stay pinned), but anything else goes
through :func:`services.llm.brief_mock.synthesize_brief_parse` — a deterministic
keyword parser that reads real Indian briefs ("3BHK, pooja room chahiye, budget 60
lakh") and emits an assumption for everything the text did not state. Its output passes
the same :class:`~services.llm.provider.SchemaGate` as everything else.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from services.common.config import WorkerSettings, get_worker_settings
from services.common.jsonschema_lite import format_errors
from services.common.logging import get_logger
from services.llm.brief_mock import synthesize_brief_parse
from services.llm.provider import gate_for
from services.llm.types import LlmResult, LlmTask, LlmUsage

log = get_logger("llm.mock")

#: Built-in corpus shipped with the package. Always loaded, so the mock works with
#: LLM_FIXTURE_DIR unset.
BUILTIN_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

#: One file per task. Each is ``{"defaultKey": str, "responses": {key: object}}``.
FIXTURE_FILES: dict[str, str] = {
    "brief.parse": "brief-parse.json",
    "copilot.ops": "copilot-commands.json",
    "rationale.write": "rationales.json",
}

_WORD = re.compile(r"[a-z0-9']+")
#: Words carrying no signal for matching a command to a fixture.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "and",
        "or",
        "is",
        "it",
        "this",
        "that",
        "please",
        "can",
        "you",
        "i",
        "we",
        "my",
        "me",
        "make",
        "do",
    }
)


class FixtureCorpusError(RuntimeError):
    """A fixture file is missing, malformed, or violates its own schema."""


class MockLlmProvider:
    """Deterministic, offline, schema-validated LLM stand-in."""

    name = "mock"
    model = "fixtures"

    def __init__(self, settings: WorkerSettings | None = None) -> None:
        self.settings = settings or get_worker_settings()
        self._corpus: dict[str, _TaskFixtures] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    async def complete_json(self, task: LlmTask) -> LlmResult:
        started = time.monotonic()
        self._ensure_loaded()
        fixtures = self._corpus.get(task.name)
        if fixtures is None:
            raise FixtureCorpusError(
                "No mock fixtures for task %r. Add %s to the fixture corpus."
                % (task.name, FIXTURE_FILES.get(task.name, task.name))
            )

        if task.name == "brief.parse":
            # Curated fixtures win on an exact/normalised hit (golden corpora stay
            # pinned); everything else is genuinely parsed. See the module docstring.
            resolved = fixtures.resolve_exact(task)
            if resolved is None:
                key, data = "synthesized", synthesize_brief_parse(task.fixture_key or task.user)
            else:
                key, data = resolved
        else:
            key, data = fixtures.resolve(task)
        gate = gate_for(task)
        payload = gate.require(json.loads(json.dumps(data)), attempts=1)
        log.info("llm.mock.answered", task=task.name, fixture_key=key)
        return LlmResult(
            data=payload,
            provider=self.name,
            model=self.model,
            usage=LlmUsage(),
            attempts=1,
            repaired=False,
            is_mock=True,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        directories = [BUILTIN_FIXTURE_DIR]
        override = self.settings.fixture_dir()
        if override != BUILTIN_FIXTURE_DIR and override.is_dir():
            directories.append(override)

        for task_name, filename in FIXTURE_FILES.items():
            fixtures = _TaskFixtures(task_name)
            for directory in directories:
                path = directory / filename
                if path.is_file():
                    fixtures.merge(_read_fixture_file(path), source=str(path))
            if not fixtures.responses:
                raise FixtureCorpusError(
                    "No fixtures found for %r. Looked for %s in: %s"
                    % (task_name, filename, ", ".join(str(d) for d in directories))
                )
            self._corpus[task_name] = fixtures
        self._loaded = True
        self._validate_corpus()

    def _validate_corpus(self) -> None:
        """Hold every fixture to the schema the real provider must satisfy."""
        from services.llm.schemas import SCHEMAS_BY_TASK

        problems: list[str] = []
        for task_name, fixtures in self._corpus.items():
            schema = SCHEMAS_BY_TASK.get(task_name)
            if schema is None:
                continue
            gate = gate_for(
                LlmTask(
                    name=task_name,  # type: ignore[arg-type]  # keys come from FIXTURE_FILES
                    system="",
                    user="",
                    schema=schema,
                    schema_name=task_name,
                )
            )
            for key, value in fixtures.responses.items():
                failures = gate.check(value)
                if failures:
                    problems.append(
                        "%s/%s:\n%s" % (task_name, key, format_errors(failures, limit=5))
                    )
        if problems:
            raise FixtureCorpusError(
                "%d mock fixture(s) violate the schema the real provider is held to. "
                "A fixture the model would not be allowed to return makes the e2e "
                "suite meaningless.\n\n%s" % (len(problems), "\n\n".join(problems))
            )

    # ------------------------------------------------------------------
    def known_keys(self, task_name: str) -> tuple[str, ...]:
        """Fixture keys for a task. Used by the copilot eval harness."""
        self._ensure_loaded()
        fixtures = self._corpus.get(task_name)
        return tuple(sorted(fixtures.responses)) if fixtures else ()


class _TaskFixtures:
    """The responses for one task plus the matching policy."""

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        self.responses: dict[str, Any] = {}
        self.default_key: str = ""
        self._tokens: dict[str, frozenset[str]] = {}

    def merge(self, document: Mapping[str, Any], *, source: str) -> None:
        responses = document.get("responses")
        if not isinstance(responses, dict):
            raise FixtureCorpusError("%s: 'responses' must be an object" % source)
        for key, value in responses.items():
            self.responses[str(key)] = value
            self._tokens[str(key)] = _tokenise(str(key))
        default = document.get("defaultKey")
        if isinstance(default, str) and default:
            self.default_key = default

    def resolve_exact(self, task: LlmTask) -> tuple[str, Any] | None:
        """An exact or normalised fixture hit, or ``None``. No fuzzy matching.

        This is the lookup ``brief.parse`` uses: a curated fixture must be pinned by
        its literal text, because a keyword-overlap "best guess" answering a brief it
        was never written for would defeat the synthesizer that handles unseen prose.
        """
        raw = task.fixture_key or task.user
        exact = raw.strip()
        if exact in self.responses:
            return exact, self.responses[exact]

        normalised = _normalise(raw)
        if normalised in self.responses:
            return normalised, self.responses[normalised]
        for key in sorted(self.responses):
            if _normalise(key) == normalised:
                return key, self.responses[key]
        return None

    def resolve(self, task: LlmTask) -> tuple[str, Any]:
        """Pick a fixture. Deterministic at every step — no randomness, no time."""
        hit = self.resolve_exact(task)
        if hit is not None:
            return hit

        best = self._best_overlap(_normalise(task.fixture_key or task.user))
        if best is not None:
            return best, self.responses[best]

        if self.default_key and self.default_key in self.responses:
            return self.default_key, self.responses[self.default_key]
        first = sorted(self.responses)[0]
        return first, self.responses[first]

    def _best_overlap(self, normalised: str) -> str | None:
        """Highest keyword overlap, ties broken by key order so it is reproducible.

        The threshold matters: a weak match that answers the wrong command is worse
        than falling through to the honest default, so at least half of the fixture's
        own keywords must be present.
        """
        wanted = _tokenise(normalised)
        if not wanted:
            return None
        best_key: str | None = None
        best_score = 0.0
        for key in sorted(self.responses):
            tokens = self._tokens.get(key) or frozenset()
            if not tokens:
                continue
            overlap = len(tokens & wanted)
            if not overlap:
                continue
            score = overlap / len(tokens)
            if score > best_score:
                best_score, best_key = score, key
        return best_key if best_score >= 0.5 else None


def _read_fixture_file(path: Path) -> Mapping[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except json.JSONDecodeError as exc:
        raise FixtureCorpusError("%s is not valid JSON: %s" % (path, exc)) from exc
    if not isinstance(document, dict):
        raise FixtureCorpusError("%s must contain a JSON object" % path)
    return document


def _normalise(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _tokenise(text: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS)


__all__ = ["BUILTIN_FIXTURE_DIR", "FIXTURE_FILES", "FixtureCorpusError", "MockLlmProvider"]
