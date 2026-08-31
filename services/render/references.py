"""The project's inspiration board, and the questions it raises before a render.

An architect collects pictures. A client sends a kitchen they love, a facade from a
magazine, a hotel bathroom. Before this, the product could take exactly one of those —
and only its *filename*, which was stored and never read. There was nothing to say
which part of the house a picture was for, what to take from it, or what to leave.

## Every reference answers four questions

A picture on its own is ambiguous: "use this kitchen" could mean the cabinet colour, the
island shape, or the light. So each one carries:

* **where** — :class:`ReferenceScope`, the part of the design it speaks to;
* **why** — what to take from it, in the architect's words;
* **ignore** — what NOT to take, which is as important and usually unsaid;
* **how** — :class:`Intent`: follow it closely, take the feel, or avoid it.

The architect writes all four. Nothing here guesses at them, and nothing here reads the
image: a module that inferred "you probably meant the cabinets" would be wrong exactly
often enough to be untrustworthy, and its mistakes would be invisible in a render.

## What this module DOES decide

Which references apply to the render being asked for, and where two of them cannot both
be honoured. Those become questions the architect answers before the render runs —
:func:`find_conflicts` — rather than a picture that quietly followed one reference and
dropped the other. A render is a thing a client is shown; the moment to resolve "which
kitchen did you mean" is before it is made, not in the meeting.

The rules are deterministic and stated below. They are not an LLM call: they must work
with no API key (``PROVIDER_LLM=mock`` is the default and the whole product runs under
it), and a question an architect is asked should be one the product can justify.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "INTENTS",
    "SCOPES",
    "Intent",
    "Reference",
    "ReferenceConflict",
    "ReferenceScope",
    "applicable_references",
    "find_conflicts",
    "reference_prompt",
]

#: Which part of the design a reference speaks to. Deliberately coarse: an architect
#: picking from a list gets a consistent vocabulary, and a free-text "where" would make
#: every reference its own scope and no two would ever be comparable — which would make
#: conflict detection impossible, and conflict detection is the point.
ReferenceScope = Literal[
    "whole-house",
    "facade",
    "interior",
    "kitchen",
    "living",
    "bedroom",
    "bathroom",
    "landscape",
    "material",
]
SCOPES: tuple[ReferenceScope, ...] = (
    "whole-house",
    "facade",
    "interior",
    "kitchen",
    "living",
    "bedroom",
    "bathroom",
    "landscape",
    "material",
)

#: How strongly to apply it.
#:
#: ``avoid`` is not a weaker ``guide`` — it is the opposite, and it earns its place
#: because "not like this" is a thing clients say constantly and no product lets them
#: record. It steers the negative prompt.
Intent = Literal["match", "guide", "avoid"]
INTENTS: tuple[Intent, ...] = ("match", "guide", "avoid")

#: Scopes that can only be seen from outside, and those only from inside. A kitchen
#: reference cannot inform a street elevation; a facade reference cannot inform a
#: kitchen interior. ``whole-house`` and ``material`` speak to both.
_EXTERIOR_ONLY: frozenset[str] = frozenset({"facade", "landscape"})
_INTERIOR_ONLY: frozenset[str] = frozenset({"interior", "kitchen", "living", "bedroom", "bathroom"})


@dataclass(frozen=True)
class Reference:
    """One picture on the board, with the architect's four answers."""

    id: str
    #: What the architect called it. Shown in the conflict question, so it has to be
    #: something they recognise — not a filename hash.
    label: str
    scope: ReferenceScope
    #: What to take from it. Empty means the reference cannot be applied at all, which
    #: is a question rather than a silent skip.
    why: str = ""
    #: What to leave. Steers the negative prompt.
    ignore: str = ""
    intent: Intent = "guide"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "scope": self.scope,
            "why": self.why,
            "ignore": self.ignore,
            "intent": self.intent,
        }


@dataclass(frozen=True)
class ReferenceConflict:
    """Something the architect must settle before the render is worth making."""

    #: ``competing`` | ``out-of-view`` | ``unusable``
    kind: str
    #: The references involved, by id.
    reference_ids: tuple[str, ...]
    #: The question, in the architect's words, naming the pictures by their labels.
    question: str
    #: What happens if they do nothing. Always stated — a question with an unknown
    #: default is one people dismiss.
    default: str

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "referenceIds": list(self.reference_ids),
            "question": self.question,
            "default": self.default,
        }


def _scene_of(preset: Any) -> str:
    return str(getattr(preset, "scene", "exterior"))


def applies_to(reference: Reference, preset: Any) -> bool:
    """Can this reference inform this render at all?"""
    scene = _scene_of(preset)
    if reference.scope in _EXTERIOR_ONLY:
        return scene == "exterior"
    if reference.scope in _INTERIOR_ONLY:
        return scene == "interior"
    return True  # whole-house and material speak to both


def applicable_references(references: Sequence[Reference], preset: Any) -> tuple[Reference, ...]:
    """The board, filtered to what this render can actually use.

    Order is preserved: the architect's own ordering is the only ranking this module
    has any business applying, and inventing one would silently prefer a picture they
    did not prefer.
    """
    return tuple(r for r in references if applies_to(r, preset) and (r.why or r.ignore))


def find_conflicts(references: Sequence[Reference], preset: Any) -> tuple[ReferenceConflict, ...]:
    """What to ask before rendering. Empty means go ahead.

    Three rules, all deterministic:

    1. **Competing.** Two references on the same scope both marked ``match``. Only one
       can be followed closely — "which kitchen did you mean" is a question with a real
       answer, and answering it in the client meeting instead is how a render loses a
       room its argument.
    2. **Out of view.** A reference that cannot inform this render — a bathroom picture
       on a street elevation. Not an error, and not silently dropped either: the
       architect chose it and deserves to know it will not be used here.
    3. **Unusable.** A reference with neither a "take this" nor a "leave this". The
       upload happened; the instruction did not.

    Deliberately NOT a rule: reading the ``why`` text of two references and deciding
    they disagree. That needs to understand English, it would be wrong sometimes, and a
    wrong question is worse than no question — the architect stops reading them.
    """
    out: list[ReferenceConflict] = []

    for reference in references:
        if not reference.why and not reference.ignore:
            out.append(
                ReferenceConflict(
                    kind="unusable",
                    reference_ids=(reference.id,),
                    question=(
                        "What should %s contribute? Say what to take from it, or what to "
                        "avoid." % reference.label
                    ),
                    default="It is skipped — an unannotated picture cannot steer a render.",
                )
            )

    for reference in references:
        if (reference.why or reference.ignore) and not applies_to(reference, preset):
            out.append(
                ReferenceConflict(
                    kind="out-of-view",
                    reference_ids=(reference.id,),
                    question=(
                        "%s is a %s reference and this is %s view. Use it anyway?"
                        % (
                            reference.label,
                            reference.scope.replace("-", " "),
                            "an exterior" if _scene_of(preset) == "exterior" else "an interior",
                        )
                    ),
                    default="It is left out of this render and stays on the board.",
                )
            )

    by_scope: dict[str, list[Reference]] = {}
    for reference in applicable_references(references, preset):
        if reference.intent == "match":
            by_scope.setdefault(reference.scope, []).append(reference)
    for scope, group in sorted(by_scope.items()):
        if len(group) > 1:
            out.append(
                ReferenceConflict(
                    kind="competing",
                    reference_ids=tuple(r.id for r in group),
                    question=(
                        "%s are all set to match closely for the %s. Which one should the "
                        "render follow?"
                        % (", ".join(r.label for r in group), scope.replace("-", " "))
                    ),
                    default="The first one is followed and the rest are treated as a guide.",
                )
            )
    return tuple(out)


def reference_prompt(references: Sequence[Reference], preset: Any) -> tuple[str, str]:
    """``(positive, negative)`` prompt fragments from the applicable references.

    ``why`` becomes something to draw; ``ignore`` becomes something not to. An ``avoid``
    reference contributes to the negative side whatever its ``why`` says, because
    "avoid" is what the architect chose it to mean.

    Returns empty strings when the board says nothing about this view — a render with no
    references must produce the prompt it produced before this feature existed.
    """
    positive: list[str] = []
    negative: list[str] = []
    for reference in applicable_references(references, preset):
        why = reference.why.strip()
        ignore = reference.ignore.strip()
        if reference.intent == "avoid":
            if why:
                negative.append(why)
        elif why:
            lead = "closely following" if reference.intent == "match" else "in the spirit of"
            positive.append("%s %s" % (lead, why))
        if ignore:
            negative.append(ignore)
    return ("; ".join(positive), ", ".join(negative))
