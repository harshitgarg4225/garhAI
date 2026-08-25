"""Option rationales (§10): solver facts → a 60-word paragraph that invents nothing.

    Rationales: solver facts → 60-word paragraph. Prompt forbids introducing facts not
    in input (list-then-write pattern).

The prompt asks for the pattern; this module *checks* it. The model must return
``factsUsed`` (verbatim copies of supplied facts) alongside the paragraph, and
:meth:`RationaleWriter.write` verifies that every entry really is one of the supplied
facts. A fabricated fact therefore shows up as a rejected rationale rather than as
confident prose about a cost nobody computed — which is the failure mode that would
actually damage an architect's trust.

Rejection is non-fatal by design: a plan option with no rationale is still a usable
plan option, so the caller gets ``verified=False`` and a deterministic fallback built
from the facts themselves. Losing the prose is much better than showing a lie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from services.common.logging import get_logger
from services.llm.prompts import RATIONALE_SYSTEM, rationale_user
from services.llm.provider import LlmProvider
from services.llm.schemas import RATIONALE_SCHEMA, RATIONALE_WORD_LIMIT
from services.llm.types import LlmError, LlmTask

log = get_logger("llm.rationale")

#: Slack over the 60-word target before a paragraph is treated as over-long. The limit
#: is a product decision (a card, not an essay); a couple of words either way is noise.
WORD_LIMIT_TOLERANCE = 8

_WORD = re.compile(r"[\w'%-]+")
#: Numbers are the part of a fabricated fact that does damage, so they are checked
#: separately from the prose: every number in the paragraph must appear in the facts.
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Rationale:
    """A rationale plus whether it survived fact-checking."""

    paragraph: str
    facts_used: tuple[str, ...] = ()
    #: True when every claim traced back to a supplied fact.
    verified: bool = True
    #: Populated when verification failed — logged, never shown to the user.
    problems: tuple[str, ...] = ()
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "paragraph": self.paragraph,
            "factsUsed": list(self.facts_used),
            "verified": self.verified,
        }


class RationaleWriter:
    """Verbalises solver facts without letting the model add any."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def write(
        self,
        facts: Sequence[str],
        *,
        option_label: str = "this option",
        fixture_key: str = "",
        max_output_tokens: int = 1_024,
    ) -> Rationale:
        clean_facts = [str(fact).strip() for fact in facts if str(fact).strip()]
        if not clean_facts:
            return Rationale(
                paragraph="",
                verified=True,
                problems=("no facts supplied",),
            )

        task = LlmTask(
            name="rationale.write",
            system=RATIONALE_SYSTEM,
            user=rationale_user(clean_facts, option_label=option_label),
            schema=RATIONALE_SCHEMA,
            schema_name="rationale",
            fixture_key=fixture_key or option_label,
            max_output_tokens=max_output_tokens,
            effort="low",
        )
        try:
            result = await self.provider.complete_json(task)
        except LlmError as exc:
            log.warning("llm.rationale.unavailable", code=exc.code, detail=exc.detail)
            return _fallback(clean_facts, problems=("provider unavailable",))

        paragraph = str(result.data.get("paragraph") or "").strip()
        facts_used = tuple(
            str(item).strip() for item in result.data.get("factsUsed") or [] if str(item).strip()
        )
        problems = verify(paragraph, facts_used, clean_facts)
        if problems:
            log.warning(
                "llm.rationale.rejected", problems=list(problems), **result.summary()
            )
            return _fallback(clean_facts, problems=problems)

        return Rationale(
            paragraph=paragraph,
            facts_used=facts_used,
            verified=True,
            meta=result.summary(),
        )


def verify(
    paragraph: str, facts_used: Sequence[str], supplied: Sequence[str]
) -> tuple[str, ...]:
    """Return the reasons this rationale should not be shown. Empty ⇒ trustworthy."""
    problems: list[str] = []

    if not paragraph:
        problems.append("empty paragraph")

    word_count = len(_WORD.findall(paragraph))
    if word_count > RATIONALE_WORD_LIMIT + WORD_LIMIT_TOLERANCE:
        problems.append(
            "paragraph is %d words, over the %d-word limit" % (word_count, RATIONALE_WORD_LIMIT)
        )

    normalised_supplied = {_normalise(fact) for fact in supplied}
    for fact in facts_used:
        if _normalise(fact) not in normalised_supplied:
            problems.append("cited a fact that was not supplied: %r" % fact[:80])

    # Every number in the prose must come from the facts. This is the cheap, high-value
    # half of fact-checking: invented *numbers* are what an architect would act on.
    supplied_numbers = set()
    for fact in supplied:
        supplied_numbers.update(_NUMBER.findall(fact))
    for number in _NUMBER.findall(paragraph):
        if number not in supplied_numbers:
            problems.append("used a number not present in the facts: %s" % number)

    return tuple(problems)


def _fallback(facts: Sequence[str], *, problems: Sequence[str]) -> Rationale:
    """Deterministic rationale assembled from the facts themselves.

    No model involved, so it cannot invent anything. Terser than the written version,
    and honest — the UI shows it as the plan's key numbers rather than as prose.
    """
    highlights = list(facts)[:3]
    paragraph = ". ".join(highlight.rstrip(".") for highlight in highlights)
    if paragraph:
        paragraph += "."
    return Rationale(
        paragraph=paragraph,
        facts_used=tuple(highlights),
        verified=False,
        problems=tuple(problems),
    )


def _normalise(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


__all__ = ["WORD_LIMIT_TOLERANCE", "Rationale", "RationaleWriter", "verify"]
