"""Compliance explanations (B-9): a failed rule → why, and what to do about it.

    "Your east setback is 1.2 m, Bengaluru wants 1.5" — plus concrete ways to fix it.

The product this belongs to sells *citable* compliance, so the sentence an architect
reads next to a red chip has to be true in a way that survives being quoted to a
municipal officer. That constrains the design far more than a rationale does:

**The citation is never model-authored.** :class:`Explanation` carries ``rule_id`` and
``cite`` copied verbatim from the rules-engine row. The output schema has no field to
put a citation in, so the worst a model can do is write a clause number in its prose —
and :func:`verify_explanation` rejects both a foreign rule id and any number that was
not supplied.

**No number may be invented.** Every numeral in the prose must equal a numeral in the
supplied facts, compared as a *value* (``1.5`` matches the engine's ``1.50 m``) rather
than as a string. This is the half of fact-checking that matters: an architect acts on
numbers, and the rules engine is the only thing entitled to produce one. There is one
source for compliance numbers, and this module is not it — it forwards them.

**No geometry, ever.** The locked decision holds here too. The explainer explains; it
does not propose coordinates. The schema gives coordinates nowhere to live, and the
verifier rejects a coordinate pair in the prose.

**It cannot fail.** :func:`compose_explanation` builds a correct, cited explanation
from the row alone — the engine's own sentence, the pack's own fix hint, the pack's own
citation, no model involved. That is the answer when the provider is down, when
verification rejects the model's prose, and when there is no API key at all. The model
path is an *enhancement* on top of an answer that is always right, which is the only
sane arrangement for a compliance claim.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from services.common.logging import get_logger
from services.llm.prompts import COMPLIANCE_EXPLAIN_SYSTEM, compliance_explain_user
from services.llm.provider import LlmProvider
from services.llm.redaction import EXPLAINER_FINDING_FIELDS, pick
from services.llm.schemas import (
    COMPLIANCE_EXPLAIN_SCHEMA,
    EXPLANATION_WORD_LIMIT,
    MAX_EXPLANATION_FIXES,
)
from services.llm.types import LlmError, LlmTask

log = get_logger("llm.explainer")

#: Only a violated rule gets explained. A passing rule needs no essay, and a
#: ``not_applicable`` one has no numbers to be truthful about.
EXPLAINABLE_STATUSES: tuple[str, ...] = ("fail", "warn")

#: Slack over the word target before the prose is treated as over-long.
WORD_LIMIT_TOLERANCE = 12

_WORD = re.compile(r"[\w'%-]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
#: A rule id: three or more dot-separated segments, e.g. ``blr.setback.front.plot``.
#: Two segments would match "e.g." and ordinary prose; three does not.
_RULE_ID_SHAPE = re.compile(r"\b[a-z0-9]+(?:\.[a-z0-9_-]+){2,}\b")
#: A coordinate pair. The one geometry shape a model reaches for unprompted.
_POINT_PAIR = re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)")


@dataclass(frozen=True)
class Explanation:
    """One finding, explained. Citation and numbers come from the engine, not a model."""

    rule_id: str
    status: str
    #: The rules engine's own sentence, verbatim. Always present, never model-written.
    headline: str
    #: The prose. Model-written when ``source == "model"``, composed otherwise.
    body: str
    fixes: tuple[str, ...] = ()
    #: Full citation and the clause alone, both copied from the row.
    cite: str = ""
    cite_short: str = ""
    #: The pack's own confidence in the value. "seed" means it needs local review
    #: before anyone submits on it — the UI must keep showing this.
    confidence: str = ""
    #: ``"model"`` (fact-checked and passed) or ``"rules"`` (composed from the row).
    source: str = "rules"
    #: Why the model's prose was rejected, when it was. Logged, never shown.
    problems: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "status": self.status,
            "headline": self.headline,
            "body": self.body,
            "fixes": list(self.fixes),
            "cite": self.cite,
            "citeShort": self.cite_short,
            "confidence": self.confidence,
            "source": self.source,
        }


class NotExplainable(ValueError):
    """The row is not a violation, so there is nothing honest to explain."""


def finding_facts(finding: Mapping[str, Any]) -> tuple[str, ...]:
    """The supplied-fact list: the ONE thing the explanation may draw numbers from.

    Every downstream check — the number allowlist, the ``factsUsed`` cross-check, the
    mock's synthesizer — reads this function, so there is a single definition of "what
    the model was told". A second, slightly different list somewhere else is how a
    verifier ends up approving a number nobody supplied.
    """
    row = pick(finding, EXPLAINER_FINDING_FIELDS)
    facts: list[str] = []
    rule_id = str(row.get("ruleId") or "")
    pack_id = str(row.get("packId") or "")
    if rule_id:
        facts.append(
            "Rule %s%s, severity %s."
            % (rule_id, (" from pack %s" % pack_id) if pack_id else "", row.get("severity") or "")
        )
    message = str(row.get("message") or "").strip()
    if message:
        facts.append("The rules engine reports: %s" % message)
    if row.get("actual") is not None or row.get("limit") is not None:
        facts.append(
            "Measured %s against a limit of %s (unit %s)."
            % (row.get("actual"), row.get("limit"), row.get("unit") or "unspecified")
        )
    cite = str(row.get("cite") or row.get("citeShort") or "").strip()
    if cite:
        facts.append("Citation: %s" % cite)
    fix_hint = str(row.get("fixHint") or "").strip()
    if fix_hint:
        facts.append("The rule pack's suggested fix: %s" % fix_hint)
    confidence = str(row.get("confidence") or "").strip()
    if confidence:
        facts.append("Confidence in this rule's value: %s." % confidence)
    return tuple(facts)


def compose_explanation(finding: Mapping[str, Any]) -> Explanation:
    """The deterministic explanation. No model, therefore nothing to fact-check.

    This is the floor the whole feature stands on: with no API key, no network and no
    provider, an architect still gets the engine's sentence, the pack's fix and the
    citation. Terser than model prose and completely true.
    """
    row = pick(finding, EXPLAINER_FINDING_FIELDS)
    status = str(row.get("status") or "")
    if status not in EXPLAINABLE_STATUSES:
        raise NotExplainable(
            "only %s findings are explained; this one is %r"
            % (" and ".join(EXPLAINABLE_STATUSES), status or "missing")
        )

    headline = str(row.get("message") or "").strip()
    cite = str(row.get("cite") or "").strip()
    cite_short = str(row.get("citeShort") or "").strip()
    fix_hint = str(row.get("fixHint") or "").strip()

    sentences = [headline] if headline else []
    sentences.append(
        "This is a hard requirement, so the drawing set cannot be submitted while it " "stands."
        if status == "fail"
        else "This is a warning, so it does not block submission, but a reviewer may " "raise it."
    )
    if cite:
        sentences.append("It comes from %s." % cite.rstrip("."))

    fixes: list[str] = []
    if fix_hint:
        fixes.append(fix_hint)
    fixes.append(
        "If the design cannot meet this, record an override with your reason — it stays "
        "visible in the compliance annexure."
    )

    return Explanation(
        rule_id=str(row.get("ruleId") or ""),
        status=status,
        headline=headline,
        body=" ".join(sentences).strip(),
        fixes=tuple(fixes[:MAX_EXPLANATION_FIXES]),
        cite=cite,
        cite_short=cite_short,
        confidence=str(row.get("confidence") or ""),
        source="rules",
        facts=finding_facts(row),
    )


def verify_explanation(
    body: str,
    fixes: Sequence[str],
    facts_used: Sequence[str],
    supplied: Sequence[str],
    *,
    rule_id: str,
) -> tuple[str, ...]:
    """Reasons this explanation must not be shown. Empty ⇒ safe to show.

    Ordered by how much damage the failure would do: a wrong number first, then a
    wrong citation, then geometry, then length.
    """
    problems: list[str] = []
    prose = " ".join([body, *fixes])

    if not body.strip():
        problems.append("empty explanation")

    supplied_numbers = _numbers_in(" ".join(supplied))
    for token in _NUMBER.findall(prose):
        value = _as_decimal(token)
        if value is None or value not in supplied_numbers:
            problems.append("used a number the rules engine did not produce: %s" % token)

    normalised_supplied = {_normalise(fact) for fact in supplied}
    for fact in facts_used:
        if _normalise(fact) not in normalised_supplied:
            problems.append("cited a fact that was not supplied: %r" % fact[:80])

    for token in _RULE_ID_SHAPE.findall(prose):
        if token != rule_id:
            problems.append("named a rule other than the one being explained: %s" % token)

    if _POINT_PAIR.search(prose):
        problems.append("proposed geometry: an explanation never emits coordinates")

    word_count = len(_WORD.findall(body))
    if word_count > EXPLANATION_WORD_LIMIT + WORD_LIMIT_TOLERANCE:
        problems.append(
            "explanation is %d words, over the %d-word limit" % (word_count, EXPLANATION_WORD_LIMIT)
        )
    if len(fixes) > MAX_EXPLANATION_FIXES:
        problems.append(
            "returned %d fixes, over the limit of %d" % (len(fixes), MAX_EXPLANATION_FIXES)
        )

    return tuple(problems)


class ComplianceExplainer:
    """Explains one rules-engine finding, refusing to let a model invent anything."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def explain(
        self,
        finding: Mapping[str, Any],
        *,
        authority: str = "",
        max_output_tokens: int = 1_024,
    ) -> Explanation:
        """Model prose when it survives fact-checking, the composed answer otherwise.

        Raises :class:`NotExplainable` for a row that is not a violation — a caller
        asking to explain a passing rule has a bug, and inventing a reason it passed
        would be exactly the fabrication this module exists to prevent.
        """
        grounded = compose_explanation(finding)
        row = pick(finding, EXPLAINER_FINDING_FIELDS)
        facts = grounded.facts

        task = LlmTask(
            name="compliance.explain",
            system=COMPLIANCE_EXPLAIN_SYSTEM,
            user=compliance_explain_user(row, authority=authority),
            schema=COMPLIANCE_EXPLAIN_SCHEMA,
            schema_name="compliance_explain",
            fixture_key=grounded.rule_id,
            max_output_tokens=max_output_tokens,
            effort="low",
        )
        try:
            result = await self.provider.complete_json(task)
        except LlmError as exc:
            log.warning("llm.explainer.unavailable", code=exc.code, rule_id=grounded.rule_id)
            return _with_problems(grounded, ("provider unavailable: %s" % exc.code,))

        body = str(result.data.get("explanation") or "").strip()
        fixes = tuple(
            str(item).strip() for item in result.data.get("fixes") or [] if str(item).strip()
        )
        facts_used = tuple(
            str(item).strip() for item in result.data.get("factsUsed") or [] if str(item).strip()
        )
        problems = verify_explanation(body, fixes, facts_used, facts, rule_id=grounded.rule_id)
        if problems:
            log.warning(
                "llm.explainer.rejected",
                rule_id=grounded.rule_id,
                problems=list(problems),
                **result.summary(),
            )
            return _with_problems(grounded, problems)

        # Citation, rule id, status and confidence still come from the row — the model
        # only ever supplied prose.
        return Explanation(
            rule_id=grounded.rule_id,
            status=grounded.status,
            headline=grounded.headline,
            body=body,
            fixes=fixes or grounded.fixes,
            cite=grounded.cite,
            cite_short=grounded.cite_short,
            confidence=grounded.confidence,
            source="model",
            facts=facts,
            meta=result.summary(),
        )


def _with_problems(grounded: Explanation, problems: Sequence[str]) -> Explanation:
    return Explanation(
        rule_id=grounded.rule_id,
        status=grounded.status,
        headline=grounded.headline,
        body=grounded.body,
        fixes=grounded.fixes,
        cite=grounded.cite,
        cite_short=grounded.cite_short,
        confidence=grounded.confidence,
        source="rules",
        problems=tuple(problems),
        facts=grounded.facts,
    )


def _numbers_in(text: str) -> set[Decimal]:
    """Every numeral in ``text`` as a value.

    Values, not strings: the engine renders "1.50 m" and an architect writes "1.5 m",
    and rejecting the second would make the verifier fire on correct output — which is
    how a real gate gets weakened until it stops firing at all.
    """
    values: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        value = _as_decimal(token)
        if value is not None:
            values.add(value)
    return values


def _as_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token).normalize()
    except InvalidOperation:  # pragma: no cover - _NUMBER only matches valid decimals
        return None


def _normalise(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


__all__ = [
    "EXPLAINABLE_STATUSES",
    "WORD_LIMIT_TOLERANCE",
    "ComplianceExplainer",
    "Explanation",
    "NotExplainable",
    "compose_explanation",
    "finding_facts",
    "verify_explanation",
]
