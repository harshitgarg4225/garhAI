"""The mock's answer for ``compliance.explain`` — synthesized, never canned (B-9).

Why this task has no fixture corpus, unlike the other three
-----------------------------------------------------------
A copilot fixture keyed on "swap the kitchen and the dining room" is right on every
project, because the answer is an op list about elements the command names. A
compliance explanation is not: it is a sentence about *this* design's numbers. A canned
"your front setback is 1.20 m, Bengaluru wants 1.50 m" would be wrong the moment the
plot changes, and wrong in the one way this product cannot afford — a fabricated
compliance number. So there is nothing to pin, and pinning it would be a lie rather
than a shortcut.

Instead the mock reconstructs the finding **from the prompt** (:func:`~services.llm.
prompts.parse_finding`) and writes an explanation using only the strings and numbers
the prompt actually carried. Two things follow, and both are worth more than a fixture
file:

* the zero-key product gives a correct, cited explanation through the real pipeline —
  the real schema gate, the real fact-checker in :mod:`services.llm.explainer`;
* if the prompt ever stops carrying a fact, the mock stops being able to use it, in
  exactly the way a frontier model would. The mock cannot paper over a prompt that
  forgot to include the limit.
"""

from __future__ import annotations

from typing import Any

from services.llm.explainer import EXPLAINABLE_STATUSES, finding_facts
from services.llm.prompts import parse_finding
from services.llm.schemas import MAX_EXPLANATION_FIXES

#: Said when the prompt could not be read back. Deliberately not a supplied fact, so
#: the explainer's verifier rejects it and falls back to the composed answer rather
#: than showing prose built on nothing.
_UNREADABLE = "the finding could not be read from the prompt"


def synthesize_explanation(user_turn: str) -> dict[str, Any]:
    """A schema-valid ``compliance.explain`` answer built only from the prompt's facts.

    Every numeral in the returned prose comes from a string the prompt supplied, so it
    passes :func:`~services.llm.explainer.verify_explanation` for the right reason:
    there is nothing in it that was not given.
    """
    finding = parse_finding(user_turn)
    if finding is None:
        return {
            "factsUsed": [_UNREADABLE],
            "explanation": "This check did not pass. See the rule's own wording below.",
            "fixes": ["Open the rule to see what it requires."],
        }

    facts = finding_facts(finding)
    message = str(finding.get("message") or "").strip()
    cite = str(finding.get("cite") or finding.get("citeShort") or "").strip()
    fix_hint = str(finding.get("fixHint") or "").strip()
    status = str(finding.get("status") or "")

    sentences: list[str] = []
    if message:
        sentences.append(message)
    sentences.append(
        "It is a hard requirement, so it has to be resolved before the set goes in."
        if status == "fail"
        else "It is a warning rather than a blocker, but a reviewer may well raise it."
    )
    if cite:
        sentences.append("The bye-law reference is %s." % cite.rstrip("."))

    fixes: list[str] = []
    if fix_hint:
        fixes.append(fix_hint)
    fixes.append(
        "Check whether a neighbouring room can give up the space before you move an "
        "external wall — it is almost always the cheaper change."
    )
    if status in EXPLAINABLE_STATUSES:
        fixes.append(
            "If the design genuinely cannot meet this, record an override with your "
            "reason so it stays visible in the compliance annexure."
        )

    return {
        # Verbatim entries of the supplied list: the list-then-write step, done
        # honestly rather than asserted.
        "factsUsed": [fact for fact in facts if fact][:12] or [_UNREADABLE],
        "explanation": " ".join(sentences).strip()
        or "This check did not pass. See the rule's own wording below.",
        "fixes": fixes[:MAX_EXPLANATION_FIXES],
    }


__all__ = ["synthesize_explanation"]
