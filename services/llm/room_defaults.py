"""Sizes for the rooms a client asked for but did not dimension.

A client says "3BHK with a pooja room". They do not say "the master bedroom is
13.5 m² with a minimum width of 3.3 m" — nobody says that, and an architect would
not ask. Something has to turn the first sentence into the second, because the
solver cannot tile a room that has no size.

Before this existed, nothing did. The parser returned ``{"type": "bedroom",
"count": 2}`` and the program layer read ``int(raw.get("minAreaMm2") or 0)`` — so
every room arrived at Stage A as a zero-area, zero-width rectangle, Stage A
reported infeasible, and the job finished "succeeded" with no options. The only
brief in the product that generated anything was the seeded demo's, whose sizes
are written out by hand in ``garh_api.seed.demo``.

## Two different numbers, two different sources

**The minimum is law.** It comes from the rule pack — the same file the compliance
tab cites — because a product selling citable compliance must not carry a second
opinion about the smallest legal bedroom. :func:`legal_minimums` reads
``rulepacks/nbc-core.json`` and nothing here restates a number that lives there.

**The target is a judgement**, and this module owns it. NBC has no opinion on how
big a comfortable master bedroom is; Indian practice does. Every target below is a
practice default expressed as a multiple of the legal minimum, and every one of them
is emitted as an :class:`Assumption` the architect sees and can overwrite — golden
rule 4: anything not stated is an assumption, never a silence.

## Why targets are multiples rather than absolutes

A hard-coded 13.5 m² master bedroom would silently contradict a city pack that set a
different habitable minimum. Expressing the target as "1.35 × the legal minimum"
means the two move together, and a pack that raises the floor raises the default
room with it instead of producing a room that is legal in one city and not another.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

__all__ = [
    "DEFAULT_TARGET_RATIO",
    "RoomDefault",
    "TARGET_RATIOS",
    "legal_minimums",
    "size_rooms",
]

#: services/llm/room_defaults.py → ../../ is the repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

#: Which rule-pack minimum governs each room type this product knows about.
#: A type absent here is sized off the habitable minimum, which is the conservative
#: answer: an unknown room in a dwelling is habitable until someone says otherwise.
_MINIMUM_RULE: Mapping[str, tuple[str, str]] = {
    # room type            (area rule id,                    width rule id)
    "living_dining": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "living": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "dining": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "bedroom": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "bedroom_master": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "master_bedroom": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "guest_bedroom": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "study": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "servant_room": ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min"),
    "kitchen": ("nbc.room.kitchen.area.min", "nbc.room.kitchen.width.min"),
    "kitchen_dining": ("nbc.room.kitchen_dining.area.min", "nbc.room.kitchen.width.min"),
    "bath": ("nbc.room.bath.area.min", "nbc.room.bath.width.min"),
    "bath_wc": ("nbc.room.bath_wc.area.min", "nbc.room.bath.width.min"),
    "wc": ("nbc.room.wc.area.min", "nbc.room.wc.width.min"),
    "toilet": ("nbc.room.wc.area.min", "nbc.room.wc.width.min"),
    # A pooja room, utility and store are NOT habitable rooms — they are service
    # spaces, and holding them to the 9.5 m² habitable floor would make a normal
    # Indian house illegal on paper. They take the WC minimum as their floor, which
    # is the smallest room the code contemplates, and a practice target above it.
    "pooja": ("nbc.room.wc.area.min", "nbc.room.wc.width.min"),
    "utility": ("nbc.room.wc.area.min", "nbc.room.wc.width.min"),
    "store": ("nbc.room.wc.area.min", "nbc.room.wc.width.min"),
}

_HABITABLE = ("nbc.room.habitable.area.min", "nbc.room.habitable.width.min")

#: How much room an Indian practice actually gives each type, as a multiple of the
#: legal minimum. Not law — the reason each one is emitted as an editable assumption.
#:
#: The master leads because it takes a wardrobe wall and a bed with room to walk both
#: sides; a second bedroom does not. Service rooms sit well above their WC-derived
#: floor because a 1.1 m² pooja room or utility is legal and useless.
TARGET_RATIOS: Mapping[str, float] = {
    "living_dining": 2.1,
    "living": 1.6,
    "dining": 1.3,
    "bedroom_master": 1.42,
    "master_bedroom": 1.42,
    "bedroom": 1.21,
    "guest_bedroom": 1.21,
    "study": 1.0,
    "servant_room": 1.0,
    "kitchen": 1.6,
    "kitchen_dining": 1.35,
    "bath": 1.9,
    "bath_wc": 1.5,
    "wc": 1.3,
    "toilet": 1.3,
    "pooja": 2.7,
    "utility": 2.9,
    "store": 2.2,
}

#: For a type nobody has thought about yet. Deliberately modest: a room this module
#: cannot name should not be handed the biggest default in the table.
DEFAULT_TARGET_RATIO = 1.2

#: Practice widths where the legal minimum is far below what the room actually needs.
#: A 2.4 m bedroom is legal and unusable — a 1.8 m bed plus a wardrobe does not fit.
#: Absent here, the legal minimum width stands.
_PRACTICE_WIDTH_MM: Mapping[str, int] = {
    "living_dining": 3300,
    "living": 3300,
    "bedroom_master": 3300,
    "master_bedroom": 3300,
    "bedroom": 3000,
    "guest_bedroom": 3000,
    "kitchen": 2400,
    "kitchen_dining": 2400,
    "bath_wc": 1500,
    "bath": 1500,
    "pooja": 1200,
    "utility": 1200,
}


@dataclass(frozen=True)
class RoomDefault:
    """The size this module would give one room type, and where each half came from."""

    room_type: str
    min_area_mm2: int
    target_area_mm2: int
    min_width_mm: int
    #: The rule-pack clause the minimum came from, for the assumption chip.
    cite: str


def _read_pack() -> Mapping[str, Any]:
    path = os.path.join(_REPO_ROOT, "rulepacks", "nbc-core.json")
    with open(path, encoding="utf-8") as handle:
        loaded: Any = json.load(handle)
    # `json.load` is `Any`, so returning it directly silently widens this function's
    # contract to "anything at all" — mypy --strict rejects it, and it is right to:
    # a pack that parsed as a list would blow up in `legal_minimums` instead of here.
    if not isinstance(loaded, dict):
        raise ValueError("rulepacks/nbc-core.json must be a JSON object, got %s" % type(loaded))
    return loaded


@lru_cache(maxsize=1)
def legal_minimums() -> Mapping[str, tuple[int, str]]:
    """``rule id → (threshold, citation)`` for every room minimum in the NBC pack.

    Read rather than restated. If the pack's habitable minimum moves, the default
    room this module produces moves with it, and the number an architect is shown in
    the brief is the same number the compliance tab will check against.
    """
    out: dict[str, tuple[int, str]] = {}
    for rule in _read_pack().get("rules") or ():
        rule_id = str(rule.get("id") or "")
        if not rule_id.startswith("nbc.room."):
            continue
        check = rule.get("check") or {}
        value = check.get("valueMm2") or check.get("valueMm")
        if isinstance(value, int) and value > 0:
            out[rule_id] = (value, str(rule.get("cite") or ""))
    return out


def default_for(room_type: str) -> RoomDefault:
    """The size this module would give ``room_type``, minimum first."""
    minimums = legal_minimums()
    area_rule, width_rule = _MINIMUM_RULE.get(room_type, _HABITABLE)
    min_area, cite = minimums.get(area_rule, (0, ""))
    min_width, _ = minimums.get(width_rule, (0, ""))
    if min_area <= 0:  # a pack with no such rule: fall back to habitable
        min_area, cite = minimums.get(_HABITABLE[0], (9_500_000, ""))
    if min_width <= 0:
        min_width, _ = minimums.get(_HABITABLE[1], (2_400, ""))

    ratio = TARGET_RATIOS.get(room_type, DEFAULT_TARGET_RATIO)
    # Round to a whole 100 mm² … no: areas are integer mm², and a target that is not
    # a round number reads as false precision on an assumption chip. 0.1 m² steps.
    target = int(round(min_area * ratio / 100_000.0)) * 100_000
    width = max(min_width, _PRACTICE_WIDTH_MM.get(room_type, 0))
    return RoomDefault(
        room_type=room_type,
        min_area_mm2=min_area,
        target_area_mm2=max(target, min_area),
        min_width_mm=width,
        cite=cite,
    )


def _fill(
    room: dict[str, Any],
    key: str,
    value: int,
    *,
    room_type: str,
    cite: str,
    why: str,
    assumptions: list[dict[str, str]],
) -> None:
    """Set ``key`` if the client did not, and record the assumption when we do.

    A stated size always wins. Overruling a number the client gave — even a strange
    one — would make the brief lie about what they asked for.
    """
    current = room.get(key)
    if isinstance(current, int) and current > 0:
        return
    room[key] = value
    assumptions.append(
        {
            "field": "brief.rooms.%s.%s" % (room_type, key),
            "value": str(value),
            "reason": why,
            "cite": cite,
        }
    )


def size_rooms(rooms: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Fill in the sizes a parsed brief left out.

    Returns ``(rooms, assumptions)``. A room that already carries a size keeps it —
    the client's own number always wins, and this never overwrites a stated one.
    Each field it does supply comes back as an assumption record naming the field,
    the value and the reason, ready to become a chip the architect can edit.

    Total by construction: a malformed entry is passed through untouched rather than
    dropped, because a brief that loses a room the client asked for is worse than one
    with a room this module could not size.
    """
    if not isinstance(rooms, list):
        return [], []

    out: list[dict[str, Any]] = []
    assumptions: list[dict[str, str]] = []
    for raw in rooms:
        if not isinstance(raw, Mapping):
            out.append(raw)
            continue
        room = dict(raw)
        room_type = str(room.get("type") or "")
        if not room_type:
            out.append(room)
            continue
        sized = default_for(room_type)
        spoken = room_type.replace("_", " ")

        _fill(
            room,
            "minAreaMm2",
            sized.min_area_mm2,
            room_type=room_type,
            cite=sized.cite,
            why="%s was not dimensioned, so it takes the code minimum of %.1f m²."
            % (spoken, sized.min_area_mm2 / 1e6),
            assumptions=assumptions,
        )
        _fill(
            room,
            "targetAreaMm2",
            sized.target_area_mm2,
            room_type=room_type,
            cite=sized.cite,
            why="No size was given, so the plan aims for %.1f m² — the usual Indian "
            "practice size for a %s. Change it and the plan re-solves around it."
            % (sized.target_area_mm2 / 1e6, spoken),
            assumptions=assumptions,
        )
        _fill(
            room,
            "minWidthMm",
            sized.min_width_mm,
            room_type=room_type,
            cite=sized.cite,
            why="%s needs at least %d mm across to be usable." % (spoken, sized.min_width_mm),
            assumptions=assumptions,
        )
        out.append(room)
    return out, assumptions
