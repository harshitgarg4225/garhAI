"""The mock provider's brief parser: deterministic keyword extraction, zero ML (§10).

This is what makes ``PROVIDER_LLM=mock`` *demoable*, not just testable: paste a real
Indian client brief — Hinglish welcome — and get a real parse back, with an assumption
chip for everything the text did not say. The locked decision ("the full app runs and
is e2e-testable with zero API keys") only means something if the mock understands
"3BHK, pooja room chahiye, budget 60 lakh, plot 30x40 north facing".

Three properties, all load-bearing:

* **Deterministic.** Same text → same output, byte for byte, on every machine. No
  randomness, no time, no dict-order dependence. The fixture corpus in
  ``fixtures/llm/brief-parse/`` pins this: each file is an input text plus the exact
  output, and the round-trip test replays every one.
* **Anything not stated → assumption, never silence.** Every value this parser fills
  in appears in ``assumptions[] {field, value, reason}``; every value the text gave
  outright is listed in ``stated``. The two lists partition the brief — the tests
  assert nothing falls between them.
* **No geometry.** Not one coordinate, not one room size. A plot mention ("30x40
  north facing") becomes an ``unclear`` note pointing at the plot step, because the
  brief carries programme, not shape — and an LLM (or its mock) never emits geometry.

Deliberately stdlib-only and import-light: no config, no logging, no pydantic. That
keeps it runnable under any Python (fixture regeneration uses the system 3.9) and
keeps the mock path free of heavyweight imports.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

#: Order in which room entries are emitted. Fixed so output is reproducible.
_ROOM_ORDER: Tuple[str, ...] = (
    "living_dining",
    "kitchen",
    "bedroom_master",
    "bedroom",
    "guest_bedroom",
    "study",
    "pooja",
    "servant_room",
    "bath_wc",
    "wc",
    "utility",
    "store",
    "balcony",
    "terrace",
    "porch",
)

#: Small-number words accepted where Indian briefs commonly spell them out
#: ("family of four", "paanch log"). Deliberately tiny — this is a parser, not NLU.
_NUMBER_WORDS: Dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "che": 6, "saat": 7, "aath": 8,
}
_NUMBER_WORD_PATTERN = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))

# ---------------------------------------------------------------------------
# Patterns. All matched case-insensitively against the raw text.
# ---------------------------------------------------------------------------
_BHK = re.compile(r"(\d+)\s*-?\s*bhk", re.IGNORECASE)
_BEDROOMS = re.compile(r"(\d+)\s*(?:bed\s*rooms?|kamre|kamra)", re.IGNORECASE)
_BATHS = re.compile(r"(\d+)\s*(?:bath\s*rooms?|baths?|toilets?|washrooms?)", re.IGNORECASE)
_KITCHENS = re.compile(r"(\d+)\s*(?:kitchens?|raso(?:i|iya)n?)", re.IGNORECASE)
_BALCONIES = re.compile(r"(\d+)\s*balcon(?:y|ies)", re.IGNORECASE)

_G_PLUS = re.compile(r"\bg\s*\+\s*(\d+)\b", re.IGNORECASE)
_N_FLOORS = re.compile(r"\b(\d+)\s*(?:floors?|storeys?|stories|manzil)\b", re.IGNORECASE)
_SINGLE_STOREY = re.compile(
    r"single[\s-]*stor(?:ey|y|ied)|single\s+floor|one\s+floor|ground\s+floor\s+only"
    r"|only\s+ground\s+floor|ek\s+manzil",
    re.IGNORECASE,
)

_CARS = re.compile(r"(\d+)\s*(?:cars?\b|gaa?di(?:ya)?n?\b|four[\s-]?wheelers?)", re.IGNORECASE)
_PARKING_MENTION = re.compile(r"parking|garage|car\s|gaa?di", re.IGNORECASE)

_FAMILY = re.compile(
    r"family\s+of\s+(\d+|%s)|(\d+)\s*(?:members?|people|log\b|jan\b)" % _NUMBER_WORD_PATTERN,
    re.IGNORECASE,
)

_BUDGET_CRORE = re.compile(
    r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d{1,2})?)\s*(?:crores?|cr\b)", re.IGNORECASE
)
_BUDGET_LAKH = re.compile(
    r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d{1,2})?)\s*(?:lakhs?|lacs?|lakh)", re.IGNORECASE
)
#: "₹75L" — the bare-L form is only a lakh when a currency marker precedes it,
#: otherwise "30x40 L shape" would become seventy-five lakh rupees of nonsense.
_BUDGET_COMPACT_L = re.compile(r"(?:₹|rs\.?|inr)\s*(\d+(?:\.\d{1,2})?)\s*l\b", re.IGNORECASE)
_BUDGET_PLAIN = re.compile(r"₹\s*([\d,]{6,})")
_BUDGET_WORD = re.compile(r"budget|lagat|kharcha?", re.IGNORECASE)

_PLOT_DIMS = re.compile(r"(\d+)\s*[x×*]\s*(\d+)")
_PLOT_MENTION = re.compile(r"\bplot\b|\bsite\b|zameen|facing", re.IGNORECASE)

_VASTU = re.compile(r"va+stu", re.IGNORECASE)
_VASTU_NEGATED = re.compile(r"(?:no|without|skip)\s+va+stu|va+stu\s+nahi", re.IGNORECASE)

#: Requests this system does not model in the MVP brief. Reported in `unclear`
#: rather than dropped (the §10 prompt makes the real provider do the same).
_OUT_OF_SCOPE: Tuple[Tuple[str, str], ...] = (
    (r"lift|elevator", "a lift"),
    (r"swimming\s*pool|\bpool\b", "a swimming pool"),
    (r"home\s+theat(?:re|er)", "a home theatre"),
    (r"solar", "solar panels"),
    (r"garden|lawn|landscap", "a garden/landscape area"),
)

_MAX_UNCLEAR = 10  # schema cap


def _first_int(pattern: "re.Pattern[str]", text: str) -> Optional[int]:
    match = pattern.search(text)
    if not match:
        return None
    for group in match.groups():
        if group is None:
            continue
        if group.isdigit():
            return int(group)
        lowered = group.lower()
        if lowered in _NUMBER_WORDS:
            return _NUMBER_WORDS[lowered]
    return None


def _rupees(text: str) -> Optional[int]:
    """Budget in whole rupees — integer out, never a float (model-core rule)."""
    crore = _BUDGET_CRORE.search(text)
    if crore:
        return int(round(float(crore.group(1)) * 10_000_000))
    lakh = _BUDGET_LAKH.search(text) or _BUDGET_COMPACT_L.search(text)
    if lakh:
        return int(round(float(lakh.group(1)) * 100_000))
    plain = _BUDGET_PLAIN.search(text)
    if plain:
        value = int(plain.group(1).replace(",", ""))
        if value >= 100_000:
            return value
    return None


def _mentions(text_lower: str, *needles: str) -> bool:
    return any(needle in text_lower for needle in needles)


def synthesize_brief_parse(text: str) -> Dict[str, Any]:
    """Free text → a ``BRIEF_PARSE_SCHEMA``-shaped object. Pure and deterministic.

    Returns ``{brief, assumptions, stated, unclear}`` exactly as the real provider
    would, so both go through the same :class:`~services.llm.provider.SchemaGate` and
    the same :class:`~services.llm.brief.BriefParser` assembly downstream.
    """
    lowered = text.lower()
    brief: Dict[str, Any] = {}
    assumptions: List[Dict[str, Any]] = []
    stated: List[str] = []
    unclear: List[str] = []

    def assume(field: str, value: Any, reason: str) -> None:
        assumptions.append({"field": field, "value": value, "reason": reason})

    # -- storeys ------------------------------------------------------------
    storeys: Optional[int] = None
    storeys_stated = False
    g_plus = _first_int(_G_PLUS, text)
    if g_plus is not None and 1 <= g_plus + 1 <= 6:
        storeys, storeys_stated = g_plus + 1, True
    elif _SINGLE_STOREY.search(text):
        storeys, storeys_stated = 1, True
    elif "triplex" in lowered:
        storeys, storeys_stated = 3, True
    elif "duplex" in lowered:
        storeys, storeys_stated = 2, True
    else:
        n_floors = _first_int(_N_FLOORS, text)
        if n_floors is not None and 1 <= n_floors <= 6:
            storeys, storeys_stated = n_floors, True
        elif n_floors is not None:
            unclear.append(
                "The brief mentions %d floors — this system models 1 to 6 storeys; "
                "please confirm the floor count." % n_floors
            )
    if storeys is None:
        storeys = 2
        assume(
            "brief.storeys",
            2,
            "The brief did not say how many floors — G+1 is the most common fit for "
            "an Indian urban plot. Change it if the client wants otherwise.",
        )
    else:
        stated.append("brief.storeys")
    brief["storeys"] = storeys

    # -- stilt / basement -----------------------------------------------------
    if "stilt" in lowered:
        brief["hasStilt"] = True
        stated.append("brief.hasStilt")
    else:
        brief["hasStilt"] = False
        assume(
            "brief.hasStilt",
            False,
            "Stilt parking was not mentioned — assumed none; turn it on for a tight plot.",
        )
    if _mentions(lowered, "basement", "tahkhana"):
        brief["hasBasement"] = True
        stated.append("brief.hasBasement")
    else:
        brief["hasBasement"] = False
        assume("brief.hasBasement", False, "A basement was not mentioned — assumed none.")

    # -- rooms ---------------------------------------------------------------
    rooms: Dict[str, Dict[str, Any]] = {}
    any_room_stated = False

    def add_room(room_type: str, count: int) -> None:
        rooms[room_type] = {"type": room_type, "count": count}

    bhk = _first_int(_BHK, text)
    bedroom_word = _first_int(_BEDROOMS, text)
    bedrooms: Optional[int] = None
    bedrooms_stated = False
    if bhk is not None and 1 <= bhk <= 10:
        bedrooms, bedrooms_stated = bhk, True
    elif bedroom_word is not None and 1 <= bedroom_word <= 10:
        bedrooms, bedrooms_stated = bedroom_word, True
    if bedrooms is None:
        bedrooms = 3
        assume(
            "brief.rooms.bedroom.count",
            3,
            "No bedroom count was given — 3BHK is the most common Indian "
            "independent-house programme.",
        )
    else:
        any_room_stated = True
    add_room("bedroom_master", 1)
    if bedrooms > 1:
        add_room("bedroom", bedrooms - 1)

    # "3BHK" states hall + kitchen by definition; otherwise the words must appear.
    kitchen_count = _first_int(_KITCHENS, text)
    kitchen_stated = (
        bhk is not None or kitchen_count is not None or _mentions(lowered, "kitchen", "rasoi")
    )
    add_room("kitchen", kitchen_count if kitchen_count is not None else 1)
    if kitchen_stated:
        any_room_stated = True
    else:
        assume(
            "brief.rooms.kitchen.count",
            1,
            "A kitchen was not mentioned — every home needs one, so we added it.",
        )
    living_stated = bhk is not None or _mentions(
        lowered, "living", "hall", "dining", "drawing room", "baithak"
    )
    add_room("living_dining", 1)
    if living_stated:
        any_room_stated = True
    else:
        assume(
            "brief.rooms.living_dining.count",
            1,
            "A living/dining space was not mentioned — added one; it is the heart of the plan.",
        )

    baths = _first_int(_BATHS, text)
    if baths is not None and 1 <= baths <= 20:
        add_room("bath_wc", baths)
        any_room_stated = True
    else:
        assumed_baths = max(2, (bedrooms + 1) // 2)
        add_room("bath_wc", assumed_baths)
        assume(
            "brief.rooms.bath_wc.count",
            assumed_baths,
            "Bathroom count was not stated — assumed one toilet per two bedrooms, "
            "minimum two.",
        )

    if _mentions(lowered, "pooja", "puja", "mandir", "prayer room", "devghar"):
        add_room("pooja", 1)
        any_room_stated = True
    if _mentions(lowered, "guest room", "guest bedroom", "guests room"):
        add_room("guest_bedroom", 1)
        any_room_stated = True
    if _mentions(lowered, "study", "home office", "office room", "wfh"):
        add_room("study", 1)
        any_room_stated = True
    if _mentions(lowered, "servant", "maid", "helper room"):
        add_room("servant_room", 1)
        any_room_stated = True
    if _mentions(lowered, "store room", "storeroom", "storage", "godown"):
        add_room("store", 1)
        any_room_stated = True
    if _mentions(lowered, "powder room"):
        add_room("wc", 1)
        any_room_stated = True
    if "balcon" in lowered:
        add_room("balcony", _first_int(_BALCONIES, text) or 1)
        any_room_stated = True
    if _mentions(lowered, "terrace", "chhat"):
        add_room("terrace", 1)
        any_room_stated = True
    if _mentions(lowered, "porch", "portico", "verandah", "veranda"):
        add_room("porch", 1)
        any_room_stated = True

    if _mentions(lowered, "utility", "wash area", "washing area"):
        add_room("utility", 1)
        any_room_stated = True
    else:
        add_room("utility", 1)
        assume(
            "brief.rooms.utility.count",
            1,
            "A utility/wash area was not asked for but is standard alongside an "
            "Indian kitchen.",
        )

    brief["rooms"] = [rooms[name] for name in _ROOM_ORDER if name in rooms]
    if any_room_stated:
        stated.append("brief.rooms")

    # -- vastu ----------------------------------------------------------------
    if _VASTU_NEGATED.search(text):
        brief["vastuMode"] = "off"
        stated.append("brief.vastuMode")
    elif _VASTU.search(text):
        brief["vastuMode"] = "strict" if "strict" in lowered else "advisory"
        stated.append("brief.vastuMode")
    elif "pooja" in rooms:
        brief["vastuMode"] = "advisory"
        assume(
            "brief.vastuMode",
            "advisory",
            "A pooja room suggests Vastu matters — set to advisory guidance, not a "
            "hard constraint.",
        )
    else:
        brief["vastuMode"] = "off"
        assume(
            "brief.vastuMode",
            "off",
            "Vastu was not mentioned — left off; switch it on any time.",
        )

    # -- budget ---------------------------------------------------------------
    budget = _rupees(text)
    if budget is not None:
        brief["budgetInr"] = budget
        stated.append("brief.budgetInr")
    else:
        if _BUDGET_WORD.search(text):
            unclear.append(
                "A budget was mentioned but the amount could not be read — please "
                "enter it as a figure."
            )
        assume(
            "brief.budgetInr",
            None,
            "No budget was given — costing will use the city benchmark ₹/sqft until "
            "you add one.",
        )

    # -- parking ----------------------------------------------------------------
    cars = _first_int(_CARS, text)
    if cars is not None and 0 <= cars <= 20:
        brief["parkingCount"] = cars
        stated.append("brief.parkingCount")
    elif _PARKING_MENTION.search(text):
        brief["parkingCount"] = 1
        assume(
            "brief.parkingCount",
            1,
            "Parking was asked for but not how many cars — assumed one covered space.",
        )
    else:
        brief["parkingCount"] = 1
        assume(
            "brief.parkingCount",
            1,
            "Parking was not mentioned — one covered car space, which most city "
            "bye-laws require.",
        )

    # -- family size --------------------------------------------------------------
    family = _first_int(_FAMILY, text)
    if family is not None and 1 <= family <= 30:
        brief["familySize"] = family
        stated.append("brief.familySize")
    else:
        assume(
            "brief.familySize",
            None,
            "Family size was not mentioned — the room programme drives the plan "
            "either way.",
        )

    # -- plot mentions → the plot step, never the brief -----------------------------
    dims = _PLOT_DIMS.search(text)
    if dims:
        unclear.append(
            "The brief mentions a %sx%s plot — plot size, facing and roads are set "
            "in the plot step; the brief holds no geometry."
            % (dims.group(1), dims.group(2))
        )
    elif _PLOT_MENTION.search(text):
        unclear.append(
            "The plot was mentioned — set its boundary, road and facing in the plot step."
        )

    # -- out-of-scope wants, reported rather than dropped ----------------------------
    for pattern, label in _OUT_OF_SCOPE:
        if re.search(pattern, lowered):
            unclear.append(
                "The client asked for %s — the MVP brief does not model it; carry it "
                "as a note for the design stage." % label
            )

    return {
        "brief": brief,
        "assumptions": assumptions,
        "stated": stated,
        "unclear": unclear[:_MAX_UNCLEAR],
    }


__all__ = ["synthesize_brief_parse"]
