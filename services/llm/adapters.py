"""API-facing adapters for the LLM pipelines (playbook §10, §11).

Why this module exists
----------------------
``services/llm`` is shaped for its own domain: :class:`~services.llm.provider.LlmProvider`
speaks ``complete_json(task)``, :class:`~services.llm.brief.BriefParser` returns a
:class:`~services.llm.brief.BriefParseResult` dataclass, and everything is ``async``.
``garh_api.routers.projects`` wants one method with one dict shape:

    ``parse_brief(*, text, known, project_id) ->
    {provider, model, data, assumptions, completeness, warnings, usage}``

Before this file, the router probed ``get_llm_provider()`` for a ``parse_brief``
attribute. No object in this package has ever had one, so the probe always failed and
``POST /projects/:id/brief/parse`` silently served a *second*, inline mock parser that
lives in the router — meaning the schema-validated pipeline, the ``stated``/``assumptions``
partition and the PII redaction in this package were unreachable dead code.

This adapter is the seam that fixes that. It is deliberately thin: no parsing logic
lives here, only shape translation, so there is still exactly one brief parser.

Async, and honestly so
----------------------
:meth:`BriefParserAdapter.parse_brief` is ``async`` because the underlying call is a
network call when ``PROVIDER_LLM=anthropic``. The router awaits any awaitable it gets
back, so a future synchronous provider still works — but nothing here pretends a
network round-trip is synchronous by hiding it behind ``asyncio.run``, which would
deadlock inside a running event loop.

Field naming
------------
:class:`~services.common.assumptions.Assumption` uses dotted paths (``brief.storeys``);
the API's ``BriefAssumption.field`` is free text and the UI renders it as a chip label.
The prefix is stripped here so a chip reads "storeys" rather than "brief.storeys",
while ``cite`` and ``reason`` pass through untouched — golden rule 4 is that every
assumption is visible *with* its reason.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from services.common.config import WorkerSettings
from services.common.logging import get_logger
from services.llm.brief import BriefParseResult, BriefParser
from services.llm.provider import LlmProvider, get_llm_provider

log = get_logger("llm.adapters")

#: Assumption/stated paths are dotted from the document root; the API talks in
#: brief-relative field names.
_BRIEF_PREFIX = "brief."


def _strip_prefix(field: str) -> str:
    return field[len(_BRIEF_PREFIX) :] if field.startswith(_BRIEF_PREFIX) else field


def _unclear_to_warning(entry: str) -> str:
    """An ``unclear`` entry becomes UI copy.

    The schema allows both shapes the model actually produces: a bare dotted path
    (``brief.familySize``) and a full question or note ("Is a separate pooja room
    needed?"). A sentence passes through verbatim; only a path gets wrapped —
    wrapping a sentence produced grammatical mush and violated §15's copy rules.
    """
    if " " in entry.strip():
        return entry
    return "We were not sure about %s — please confirm it." % _strip_prefix(entry)


class BriefParserAdapter:
    """Adapts :class:`BriefParser` to the dict contract the API route expects."""

    def __init__(self, provider: LlmProvider) -> None:
        self._provider = provider
        self._parser = BriefParser(provider)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def parse_brief(
        self,
        *,
        text: str,
        known: Mapping[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        """Free text -> ``{provider, data, assumptions, completeness, warnings}``.

        ``project_id`` is accepted for parity with the route's call signature and for
        log correlation. It is deliberately NOT sent to the model: §13's
        prompt-injection and PII rules mean the prompt carries the client's words and
        nothing that identifies the tenant.

        Persists nothing, on purpose: a parse is a *suggestion*. The client shows the
        assumptions as chips, the architect reviews, and only then does the UI dispatch
        a ``brief.update`` op through the sequencer — the same undoable path as typing
        into the form.
        """
        result: BriefParseResult = await self._parser.parse(text, known=known)
        log.info(
            "llm.brief.adapted",
            project_id=project_id,
            provider=self.provider_name,
            assumption_count=len(result.assumptions),
            unclear_count=len(result.unclear),
        )
        return {
            "provider": self.provider_name,
            "model": str(result.meta.get("model") or ""),
            "data": dict(result.brief),
            "assumptions": [
                {
                    "field": _strip_prefix(item.field),
                    "value": item.value,
                    "reason": item.reason,
                    "cite": item.cite,
                }
                for item in result.assumptions
            ],
            "completeness": result.completeness(),
            # `unclear` is the model saying "I could not tell" — a warning to the
            # architect, not an assumption we made on their behalf.
            "warnings": [_unclear_to_warning(entry) for entry in result.unclear],
            # Token accounting for the caller's credit_events row (§2 metering).
            # Zeros for the mock — a mock row must be recorded AND distinguishable.
            "usage": {
                name: int(result.meta.get(name) or 0)
                for name in (
                    "inputTokens",
                    "outputTokens",
                    "cacheReadTokens",
                    "cacheWriteTokens",
                )
            },
        }

    async def aclose(self) -> None:
        await self._provider.aclose()


def get_brief_parser(settings: Optional[WorkerSettings] = None) -> BriefParserAdapter:
    """The factory ``garh_api.routers.projects`` looks for.

    Honours ``PROVIDER_LLM`` exactly like :func:`get_llm_provider` — ``mock`` by
    default, so the endpoint is fully functional with zero API keys.
    """
    return BriefParserAdapter(get_llm_provider(settings))


__all__ = ["BriefParserAdapter", "get_brief_parser"]
