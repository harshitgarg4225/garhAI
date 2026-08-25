"""Assumption chips — golden rule 4: "Assumptions are visible".

    "Every default the AI used (room size, floor height, ₹/sqft) is an editable chip
     in the UI with a citation where one exists (NBC clause, city bye-law)."

The same three fields appear in two places the playbook names separately — §10's brief
parse output (``assumptions[] {field, value, reason}``) and §5.1's envelope shrink
("record the assumption chip"). One type, one JSON shape, so the UI renders both with
the same component and neither can quietly drop a default it applied.

``value`` is JSON-able and, for anything dimensional, an **integer millimetre** count —
the display layer converts to ft-in / m / gaj at the boundary, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Assumption:
    """One default the system chose on the user's behalf."""

    #: Dotted path into the brief/model this assumption fills in, e.g.
    #: ``"brief.rooms.bedroom2.targetAreaMm2"`` or ``"envelope.footprintAreaMm2"``.
    field: str
    #: The value that was assumed. Integer mm for lengths, mm² for areas.
    value: Any
    #: One plain sentence, in the tone of §15: warm, specific, never blaming.
    reason: str
    #: Optional citation — NBC clause, bye-law table, or a benchmark source.
    cite: str | None = None
    #: Where it came from, so the UI can group chips.
    source: str = "system"

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "field": self.field,
            "value": self.value,
            "reason": self.reason,
            "source": self.source,
        }
        if self.cite:
            out["cite"] = self.cite
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Assumption:
        return cls(
            field=str(data["field"]),
            value=data.get("value"),
            reason=str(data.get("reason", "")),
            cite=data.get("cite"),
            source=str(data.get("source", "system")),
        )


def assumptions_to_json(items: tuple[Assumption, ...] | list[Assumption]) -> list[dict[str, Any]]:
    return [item.to_json() for item in items]


__all__ = ["Assumption", "assumptions_to_json"]
