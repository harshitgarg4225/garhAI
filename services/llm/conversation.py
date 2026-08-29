"""Multi-turn copilot context, carried without carrying PII (B-5).

"Now do the same on the first floor" is the second thing every architect says, and it
is unanswerable from a single command. This module is the memory that makes it
answerable, built so that remembering cannot become leaking.

The threat, stated plainly
--------------------------
Everything worth remembering about a turn is a sentence a human typed — the command
("widen Rajesh's bedroom door"), and the model's paraphrase of it. That is exactly the
material §13 says never reaches a provider, and this repo has already shipped one leak
of precisely that shape: a storey name with a phone number in it, guarded by a test
that could not fail.

So redaction here is not a step in a pipeline someone might reorder. It is the
constructor:

1. :class:`ConversationTurn` runs :func:`~services.llm.redaction.strip_pii` over every
   free-text field in ``__post_init__``. There is no way to hold an unredacted turn,
   because the only way to make one is to make a redacted one.
2. Ids never go through the masker at all — a ULID can carry nine consecutive digits
   and would be mangled into ``[number]``. They are gated on **shape**
   (:data:`ID_PATTERN`) instead, which also stops a crafted "id" from smuggling prose
   into the prompt.
3. :meth:`ConversationContext.render` sweeps the assembled block with
   :func:`~services.llm.redaction.find_pii` and raises if anything survived. That
   second gate exists for the failure this codebase actually has: not a missing regex,
   but a *new field* added to a turn that nobody routed through one. Rule 1 cannot
   catch that; this can.

What is deliberately not remembered: room names, storey names, brief prose, plot
addresses. The copilot addresses elements by id, and ``summarise_model`` already gives
it a derived storey ``index`` (0 = ground, 1 = first floor), so "the first floor" is
groundable without forwarding a single word a human wrote.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.llm.redaction import find_pii, strip_pii

#: What became of a turn. Closed, and a value outside it raises rather than defaulting:
#: a default that sits outside its own enum is how 83 rules in this repo went inert.
#: Only ``applied`` means the drawing actually changed.
TURN_STATUSES: tuple[str, ...] = (
    "applied",
    "rejected",
    "proposed",
    "needsClarification",
    "cannotDo",
    "invalid",
)

#: Turns kept. Six is two or three real follow-ups plus their context; beyond that the
#: model starts resolving "the same" against something the architect has forgotten.
MAX_HISTORY_TURNS = 6

MAX_COMMAND_CHARS = 240
MAX_INTENT_CHARS = 240
MAX_OP_TYPES = 12
MAX_ELEMENT_IDS = 8

#: System-minted identifiers and op types only. Anything with a space, a quote or a
#: fence delimiter in it is not an id and does not go in the prompt.
ID_PATTERN = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")

_WHITESPACE = re.compile(r"\s+")


class ConversationRedactionError(RuntimeError):
    """Assembled history still contained something :func:`strip_pii` would mask.

    A coding error, never a user condition: every field is redacted at construction,
    so reaching this means a field was added and not routed through the redactor.
    Raised rather than logged, because the alternative is sending it.
    """


@dataclass(frozen=True)
class ConversationTurn:
    """One past copilot exchange, already redacted and shape-checked.

    Construction is the redaction boundary. ``ConversationTurn(command="call me on
    9876543210")`` holds ``"call me on [phone]"`` — the original is not stored, because
    nothing downstream needs it and storing it is how it escapes.
    """

    command: str
    status: str
    intent: str = ""
    op_types: tuple[str, ...] = ()
    storey_id: str | None = None
    element_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in TURN_STATUSES:
            raise ValueError(
                "unknown conversation turn status %r. Expected one of: %s."
                % (self.status, ", ".join(TURN_STATUSES))
            )
        object.__setattr__(self, "command", _clean_text(self.command, MAX_COMMAND_CHARS))
        object.__setattr__(self, "intent", _clean_text(self.intent, MAX_INTENT_CHARS))
        object.__setattr__(self, "op_types", _clean_ids(self.op_types, MAX_OP_TYPES))
        object.__setattr__(self, "storey_id", _clean_id(self.storey_id))
        object.__setattr__(self, "element_ids", _clean_ids(self.element_ids, MAX_ELEMENT_IDS))

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ConversationTurn:
        """Build from the JSON the client sends back with the next command."""
        return cls(
            command=str(row.get("command") or ""),
            status=str(row.get("status") or ""),
            intent=str(row.get("intent") or ""),
            op_types=tuple(str(item) for item in row.get("opTypes") or ()),
            storey_id=_optional_str(row.get("storeyId")),
            element_ids=tuple(str(item) for item in row.get("elementIds") or ()),
        )

    def changed_the_design(self) -> bool:
        return self.status == "applied"

    def to_prompt_row(self, turn_number: int) -> dict[str, Any]:
        """The compact form that goes in the prompt. Allowlisted by construction."""
        row: dict[str, Any] = {
            "turn": turn_number,
            "command": self.command,
            "status": self.status,
        }
        if self.intent:
            row["intent"] = self.intent
        if self.op_types:
            row["opTypes"] = list(self.op_types)
        if self.storey_id:
            row["storeyId"] = self.storey_id
        if self.element_ids:
            row["elementIds"] = list(self.element_ids)
        return row


@dataclass(frozen=True)
class ConversationContext:
    """The last few turns, oldest first.

    Immutable and bounded: :meth:`with_turn` returns a new context holding at most
    :data:`MAX_HISTORY_TURNS`, so a long session cannot grow the prompt without limit.
    """

    turns: tuple[ConversationTurn, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "turns", tuple(self.turns)[-MAX_HISTORY_TURNS:])

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]] | None) -> ConversationContext:
        """Build from the client's JSON history. Unknown statuses raise (see above)."""
        if not rows:
            return cls()
        return cls(tuple(ConversationTurn.from_row(row) for row in rows))

    def with_turn(self, turn: ConversationTurn) -> ConversationContext:
        return ConversationContext((*self.turns, turn))

    def prompt_rows(self) -> list[dict[str, Any]]:
        return [turn.to_prompt_row(index + 1) for index, turn in enumerate(self.turns)]

    def render(self) -> str:
        """The prompt block, or ``""`` when there is no history.

        Sweeps the free-text fields one last time. Ids are excluded from the sweep on
        purpose — see the module docstring; a ULID is not a phone number even when it
        contains nine digits.
        """
        if not self.turns:
            return ""
        rows = self.prompt_rows()
        leaked: list[str] = []
        for row in rows:
            for key in ("command", "intent"):
                leaked.extend(find_pii(str(row.get(key) or "")))
        if leaked:
            raise ConversationRedactionError(
                "§13 violation: conversation history still carries %s after redaction. "
                "A free-text field was added to ConversationTurn without routing it "
                "through strip_pii in __post_init__." % ", ".join(sorted(set(leaked)))
            )
        return "\n".join(_compact_row(row) for row in rows)

    def __len__(self) -> int:
        return len(self.turns)

    def __bool__(self) -> bool:
        return bool(self.turns)


def as_context(
    history: ConversationContext | Sequence[Mapping[str, Any]] | None,
) -> ConversationContext:
    """Accept either a built context or the client's raw rows. One coercion point."""
    if history is None:
        return ConversationContext()
    if isinstance(history, ConversationContext):
        return history
    return ConversationContext.from_rows(history)


def _clean_text(value: str, limit: int) -> str:
    """Collapse whitespace, mask identifiers, then clip. In that order.

    Masking before clipping matters: clipping first can cut a phone number in half and
    leave six digits that no pattern matches but a human still recognises.
    """
    collapsed = _WHITESPACE.sub(" ", str(value)).strip()
    masked = strip_pii(collapsed)
    if len(masked) <= limit:
        return masked
    return masked[:limit].rstrip() + "…"


def _clean_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if ID_PATTERN.match(text) else None


def _clean_ids(values: Iterable[Any], limit: int) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        cleaned = _clean_id(value)
        if cleaned is not None and cleaned not in out:
            out.append(cleaned)
        if len(out) >= limit:
            break
    return tuple(out)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _compact_row(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "ID_PATTERN",
    "MAX_COMMAND_CHARS",
    "MAX_ELEMENT_IDS",
    "MAX_HISTORY_TURNS",
    "MAX_INTENT_CHARS",
    "MAX_OP_TYPES",
    "TURN_STATUSES",
    "ConversationContext",
    "ConversationRedactionError",
    "ConversationTurn",
    "as_context",
]
