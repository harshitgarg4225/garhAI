"""Brief parsing (§10): free text → Brief object + visible assumptions.

    output schema = the Brief object + `assumptions[] {field, value, reason}`.
    Anything not stated → assumption, never silence. Show all assumptions as chips.

Golden rule 4 is the whole point of this module, and it is enforced structurally rather
than hoped for: :meth:`BriefParser.parse` cross-checks the returned brief against the
assumptions list and **promotes any unexplained value into an assumption itself**. A
model that fills in ``storeys: 2`` and forgets to say so still produces a chip — one
with an honest "we filled this in" reason. Silence is not reachable from here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.common.assumptions import Assumption
from services.common.logging import get_logger
from services.llm.prompts import BRIEF_PARSE_SYSTEM, brief_parse_user
from services.llm.provider import LlmProvider
from services.llm.schemas import BRIEF_PARSE_SCHEMA
from services.llm.types import LlmResult, LlmTask

log = get_logger("llm.brief")

#: Brief fields that are dimensional/behavioural enough that an unexplained value would
#: mislead. Scalars only — room lists get one chip per room type instead.
_TRACKED_SCALARS = (
    "storeys",
    "hasStilt",
    "hasBasement",
    "vastuMode",
    "budgetInr",
    "parkingCount",
    "familySize",
)


@dataclass(frozen=True)
class BriefParseResult:
    """A parsed brief plus every default that went into it."""

    brief: dict[str, Any]
    assumptions: tuple[Assumption, ...]
    #: Dotted paths the brief text stated outright. The complement of `assumptions`.
    stated: tuple[str, ...] = ()
    unclear: tuple[str, ...] = ()
    #: Provenance from the provider call (mock vs model, tokens, repairs).
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "brief": self.brief,
            "assumptions": [item.to_json() for item in self.assumptions],
            "stated": list(self.stated),
            "unclear": list(self.unclear),
        }

    def completeness(self) -> int:
        """0-100 hint for the brief completeness meter (§ spec F2).

        Counts how much of the brief the client actually stated, rather than how much
        we filled in. Deliberately simple and explainable — an architect should be able
        to see why the meter moved when they add a sentence.
        """
        stated_paths = set(self.stated)
        assumed = {item.field for item in self.assumptions}
        filled = 0
        possible = len(_TRACKED_SCALARS) + 1  # +1 for the room programme
        for name in _TRACKED_SCALARS:
            path = "brief.%s" % name
            if path in stated_paths and path not in assumed:
                filled += 1
        rooms = self.brief.get("rooms")
        if isinstance(rooms, list) and rooms and "brief.rooms" not in assumed:
            filled += 1
        return int(round(100 * filled / possible))


class BriefParser:
    """Turns a client's words into a brief, with every default made visible."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def parse(
        self,
        text: str,
        *,
        known: Mapping[str, Any] | None = None,
        max_output_tokens: int = 4_096,
    ) -> BriefParseResult:
        task = LlmTask(
            name="brief.parse",
            system=BRIEF_PARSE_SYSTEM,
            user=brief_parse_user(text, known=known),
            schema=BRIEF_PARSE_SCHEMA,
            schema_name="brief_parse",
            fixture_key=text.strip() or "empty",
            max_output_tokens=max_output_tokens,
            effort="low",
        )
        result = await self.provider.complete_json(task)
        return self._assemble(result, stated=known or {})

    # ------------------------------------------------------------------
    def _assemble(self, result: LlmResult, *, stated: Mapping[str, Any]) -> BriefParseResult:
        brief = dict(result.data.get("brief") or {})
        assumptions = [
            Assumption.from_json(dict(item))
            for item in result.data.get("assumptions") or []
            if isinstance(item, Mapping)
        ]
        assumptions = [
            Assumption(
                field=item.field,
                value=item.value,
                reason=item.reason,
                cite=item.cite,
                source="brief-parse",
            )
            for item in assumptions
        ]
        declared_stated = {
            str(item) for item in result.data.get("stated") or [] if isinstance(item, str)
        }
        # Values supplied by the form are stated by definition — the client typed them.
        declared_stated.update("brief.%s" % name for name in stated)
        promoted = _promote_unexplained(brief, assumptions, stated=declared_stated)
        assumptions.extend(promoted)

        unclear = tuple(
            str(item) for item in (result.data.get("unclear") or []) if isinstance(item, str)
        )
        log.info(
            "llm.brief.parsed",
            assumption_count=len(assumptions),
            promoted_count=len(promoted),
            stated_count=len(declared_stated),
            unclear_count=len(unclear),
            **result.summary(),
        )
        return BriefParseResult(
            brief=brief,
            assumptions=tuple(assumptions),
            stated=tuple(sorted(declared_stated)),
            unclear=unclear,
            meta=result.summary(),
        )


def _promote_unexplained(
    brief: Mapping[str, Any],
    assumptions: Sequence[Assumption],
    *,
    stated: set[str],
) -> list[Assumption]:
    """Manufacture a chip for any tracked value that arrived without an explanation.

    This is the structural half of golden rule 4: the prompt *asks* for assumptions,
    this *guarantees* them. A value is promoted only when it is in neither list — a
    field the client actually stated must never be chipped as "we filled this in", or
    the chips stop meaning anything.

    The generated reason is deliberately plain about its own origin rather than
    inventing a justification the model never gave.
    """
    explained = {item.field for item in assumptions}
    out: list[Assumption] = []
    for name in _TRACKED_SCALARS:
        if name not in brief:
            continue
        path = "brief.%s" % name
        if path in explained or path in stated:
            continue
        out.append(
            Assumption(
                field=path,
                value=brief[name],
                reason=(
                    "We filled this in for you — the brief didn't mention it. "
                    "Change it if it's wrong."
                ),
                source="brief-parse-default",
            )
        )
    return out


__all__ = ["BriefParseResult", "BriefParser"]
