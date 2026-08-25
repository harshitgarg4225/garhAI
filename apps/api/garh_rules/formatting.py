from __future__ import annotations

"""Turning integers into the one-line human sentence on a compliance chip.

Two jobs, both narrow:

**Numbers.** A rule's ``message`` carries ``{element} {actual} {limit} {cite}``
and the schema says numbers are formatted per ``resultUnit`` — mm2 as m2, mm as
metres, counts as counts. The stated convention is 2 decimals; this module
renders **2 decimals when that is exact and more when it is not**, because a
1 mm shortfall rounded to 2 decimals produces "has 2.75 m, needs at least 2.75 m",
which is worse than ugly — it is wrong-looking. So a value keeps as many decimals
as it takes to stay exact (max 3 for a length, 6 for an area, which is exactly
what millimetres are), with trailing zeros trimmed back to two. Same choice, and
the same reasoning, as the fixture generator's prose.

Everything is ASCII: ``m2``, not ``m²``. The packs' own ``fix`` strings are ASCII
("Enlarge the room to at least 9.50 m2"), and a chip that mixes the two in one
sentence looks like a bug.

**Labels.** ``{element}`` needs to read like a drawing, not like a database:
"Bedroom 2", "The front setback", "The Bedroom 2 door". §15: plain, warm,
professional, and never blaming the user.
"""

import math
from fractions import Fraction
from typing import Any, Mapping, Optional, Sequence

from .context import (
    EvaluationContext,
    OpeningSummary,
    PlotEdge,
    ProjectionSummary,
    RoomSummary,
    ServiceElementSummary,
    StairSummary,
    StoreySummary,
)
from .zones import format_zone_list

__all__ = [
    "format_length_mm",
    "format_area_mm2",
    "format_count",
    "format_ratio",
    "format_percent",
    "format_value",
    "format_limit",
    "render_message",
    "PROJECT_LABEL",
    "edge_label",
    "opening_label",
    "projection_label",
    "room_label",
    "service_label",
    "stair_label",
    "storey_label",
    "ROOM_TYPE_LABELS",
    "SERVICE_KIND_LABELS",
    "EDGE_ROLE_LABELS",
]

PROJECT_LABEL = "The design"

EDGE_ROLE_LABELS: Mapping[str, str] = {
    "front": "front",
    "rear": "rear",
    "side-a": "left side",
    "side-b": "right side",
    "other": "boundary",
}

SERVICE_KIND_LABELS: Mapping[str, str] = {
    "water_tank": "The water tank",
    "oht": "The overhead tank",
    "sump": "The sump",
    "septic_tank": "The septic tank",
    "borewell": "The borewell",
    "meter_room": "The meter room",
    "generator": "The generator",
}

ROOM_TYPE_LABELS: Mapping[str, str] = {
    "living": "Living",
    "dining": "Dining",
    "living_dining": "Living / Dining",
    "bedroom": "Bedroom",
    "master_bedroom": "Master Bedroom",
    "guest_bedroom": "Guest Bedroom",
    "servant_room": "Servant Room",
    "kitchen": "Kitchen",
    "kitchen_dining": "Kitchen / Dining",
    "bath": "Bathroom",
    "wc": "WC",
    "bath_wc": "Bath + WC",
    "pooja": "Pooja",
    "study": "Study",
    "store": "Store",
    "utility": "Utility",
    "garage": "Garage",
    "balcony": "Balcony",
    "terrace": "Terrace",
    "courtyard": "Courtyard",
    "corridor": "Corridor",
    "lobby": "Lobby",
    "staircase": "Staircase",
    "shaft": "Shaft",
    "porch": "Porch",
    "stilt": "Stilt",
    "mumty": "Mumty",
    "other": "Room",
}

_STOREY_ORDINALS = (
    "Ground floor",
    "First floor",
    "Second floor",
    "Third floor",
    "Fourth floor",
    "Fifth floor",
)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def _decimal(whole: int, fraction: int, digits: int, minimum_digits: int = 2) -> str:
    """``123`` + ``450`` /3 digits -> ``"123.45"``. Exact; trailing zeros trimmed to 2."""
    text = ("%0*d" % (digits, fraction)).rstrip("0")
    while len(text) < minimum_digits:
        text += "0"
    return "%d.%s" % (whole, text)


def format_length_mm(value: int) -> str:
    """``2400 -> '2.40 m'``, ``750 -> '750 mm'``, ``2749 -> '2.749 m'``.

    Under a metre stays in millimetres: NBC's door and riser minima are quoted
    that way ("at least 750 mm"), and "0.75 m" reads like a different rule.
    """
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude < 1000:
        return "%s%d mm" % (sign, magnitude)
    return "%s%s m" % (sign, _decimal(magnitude // 1000, magnitude % 1000, 3))


def format_area_mm2(value: int) -> str:
    """``9500000 -> '9.50 m2'``; ``9496960 -> '9.49696 m2'`` (exact, never rounded up)."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    return "%s%s m2" % (sign, _decimal(magnitude // 1_000_000, magnitude % 1_000_000, 6))


def format_count(value: int) -> str:
    return "%d" % value


def format_ratio(value: "Fraction", decimals: int = 2) -> str:
    """A FAR or coverage ratio as a fixed-decimal string: ``1.82``, ``0.60``.

    Half-up on the last digit, computed on the exact rational so the printed value
    is the correctly rounded one — not a float that happened to land nearby. This
    is the "1.82 vs 1.75" the area statement and the FAR chip both read from.
    """
    scale = 10 ** decimals
    scaled = value * scale
    whole = math.floor(scaled + Fraction(1, 2))
    sign = "-" if whole < 0 else ""
    whole = abs(whole)
    if decimals == 0:
        return "%s%d" % (sign, whole)
    return "%s%d.%0*d" % (sign, whole // scale, decimals, whole % scale)


def format_percent(value: "Fraction", decimals: int = 1) -> str:
    return "%s%%" % format_ratio(value * 100, decimals)


def format_value(value: Any, unit: str) -> str:
    """Format ``actual``/``limit`` for its ``resultUnit``."""
    if value is None:
        return "not measured"
    if unit == "mm" and isinstance(value, int) and not isinstance(value, bool):
        return format_length_mm(value)
    if unit == "mm2" and isinstance(value, int) and not isinstance(value, bool):
        return format_area_mm2(value)
    if unit == "count" and isinstance(value, int) and not isinstance(value, bool):
        return format_count(value)
    if unit == "bp10000" and isinstance(value, int) and not isinstance(value, bool):
        # ten-thousandths -> a percentage a human can read
        return "%s%%" % _decimal(value // 100, value % 100, 2)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return format_zone_list([str(v) for v in value])
    return str(value)


def format_limit(limit: Any, unit: str) -> str:
    """Same as :func:`format_value`, plus the ``zone_check`` limit object.

    For a zone rule the limit is ``{allow, deny, fallback}``; the sentence wants
    the preferred directions, or the forbidden ones when a rule only forbids
    ("Vastu treats NE as the one placement to avoid entirely").
    """
    if isinstance(limit, Mapping):
        allow = [str(z) for z in (limit.get("allow") or ())]
        if allow:
            return format_zone_list(allow)
        deny = [str(z) for z in (limit.get("deny") or ())]
        return format_zone_list(deny)
    return format_value(limit, unit)


def render_message(
    template: str,
    *,
    element: str,
    actual: Any,
    limit: Any,
    unit: str,
    cite: str,
) -> str:
    """Substitute the four placeholders. Unknown braces are left alone.

    Deliberately not ``str.format``: a pack is data written by an architect, and a
    stray ``{`` in a citation must not raise. Only the four documented tokens are
    replaced.
    """
    out = template
    out = out.replace("{element}", element)
    out = out.replace("{actual}", format_value(actual, unit))
    out = out.replace("{limit}", format_limit(limit, unit))
    out = out.replace("{cite}", cite)
    return out


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def room_label(room: RoomSummary) -> str:
    name = (room.name or "").strip()
    if name and name.lower() not in (room.type, room.raw_type):
        return name
    return ROOM_TYPE_LABELS.get(room.type, "Room")


def storey_label(storey: StoreySummary) -> str:
    if 0 <= storey.index < len(_STOREY_ORDINALS):
        return _STOREY_ORDINALS[storey.index]
    return "Floor %d" % storey.index


def edge_label(edge: PlotEdge) -> str:
    return "The %s setback" % EDGE_ROLE_LABELS.get(edge.role, edge.role)


def opening_label(opening: OpeningSummary, context: Optional[EvaluationContext] = None) -> str:
    """"The Bedroom 2 door" when we can name the room it serves, else its role."""
    kind = opening.kind if opening.kind != "ventilator" else "ventilator"
    if context is not None and opening.room_ids:
        by_id = {r.id: r for r in context.model.rooms}
        for room_id in reversed(opening.room_ids):
            room = by_id.get(room_id)
            if room is not None:
                return "The %s %s" % (room_label(room), kind)
    if opening.role == "main-entrance":
        return "The main %s" % kind
    return "The %s %s" % (opening.role.replace("-", " "), kind)


def stair_label(stair: StairSummary) -> str:
    return "The staircase"


def projection_label(projection: ProjectionSummary) -> str:
    return "The %s" % projection.element.replace("-", " ")


def service_label(service: ServiceElementSummary) -> str:
    return SERVICE_KIND_LABELS.get(service.kind, "The %s" % service.kind.replace("_", " "))


def join_labels(labels: Sequence[str]) -> str:
    items = [label for label in labels if label]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "%s and %s" % (", ".join(items[:-1]), items[-1])
