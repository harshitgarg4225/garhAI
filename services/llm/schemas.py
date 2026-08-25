"""JSON Schemas for every §10 structured output.

These are the contract between the prompt and the code. Both providers validate
against them — the mock at fixture-load time, the real one on every response — so a
fixture that would not have been accepted from the model is a test failure, not a
convenient stand-in.

Three schema-design rules, all load-bearing:

1. **``additionalProperties: false`` everywhere.** A model that invents a field is
   telling us the prompt is unclear; silently dropping it hides that.
2. **No free-form geometry.** The copilot schema's ``ops`` array is validated against
   ``ops.schema.json`` separately (see :mod:`services.llm.op_catalog`) — here it is
   only "an array of objects with a string ``type``". Keeping the two checks separate
   means the op taxonomy stays owned by ``packages/model`` and this file never has to
   mirror it.
3. **Closed lists are generated, not copied.** Room types and Vastu modes come out of
   ``packages/model/schema/common.schema.json`` at import time — the same
   cross-language contract the TS and Python model mirrors are pinned to — so a room
   type added to the model appears here without anyone remembering a second file, and
   a fixture (or a real model response) naming a room the solver cannot place is a
   schema violation, not a silent no-op. Same single-source-of-truth rule §10 already
   applies to the op catalog.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: services/llm/schemas.py → services/llm → services → <repo root>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMON_SCHEMA_PATH = _REPO_ROOT / "packages" / "model" / "schema" / "common.schema.json"


def _model_enum(def_name: str) -> tuple[str, ...]:
    """Read a closed list out of the model's cross-language schema contract.

    Fails loudly at import: an LLM layer that cannot see the model's enums would
    validate against a guess, and every downstream "the mock is held to the same
    schema as the real provider" claim would be resting on that guess.
    """
    try:
        with _COMMON_SCHEMA_PATH.open(encoding="utf-8") as handle:
            document = json.load(handle)
        values = document["$defs"][def_name]["enum"]
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(
            "services/llm needs %s from %s (the packages/model schema contract) and "
            "could not read it: %s. The LLM schemas are generated from the model — "
            "run from a full checkout." % (def_name, _COMMON_SCHEMA_PATH, exc)
        ) from exc
    return tuple(str(value) for value in values)


#: Generated from the model contract. The Python mirror (``garh_model.model.ROOM_TYPES``)
#: is pinned to the same file by its own tests, so all three agree by construction.
ROOM_TYPE_ENUM: tuple[str, ...] = _model_enum("RoomType")
VASTU_MODE_ENUM: tuple[str, ...] = _model_enum("VastuMode")

#: Cap on emitted ops per copilot turn. A command that genuinely needs more than this
#: is a solver job, not a copilot edit (§10 scope boundary).
MAX_COPILOT_OPS = 40

#: §10: "solver facts → 60-word paragraph".
RATIONALE_WORD_LIMIT = 60


def _string(**extra: Any) -> dict[str, Any]:
    return {"type": "string", **extra}


# ---------------------------------------------------------------------------
# Brief parse (§10) — free text → Brief + assumptions[]
# ---------------------------------------------------------------------------
#: Assumption chip. Mirrors ``services.common.assumptions.Assumption`` exactly so the
#: LLM output drops straight into the same UI component as the solver's chips.
ASSUMPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field", "value", "reason"],
    "properties": {
        "field": _string(
            minLength=1,
            maxLength=120,
            description="Dotted path this assumption fills in, e.g. brief.floors.",
        ),
        "value": {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
                {"type": "boolean"},
                {"type": "null"},
            ],
            "description": "The assumed value. Lengths are integer millimetres.",
        },
        "reason": _string(
            minLength=1,
            maxLength=240,
            description="One plain sentence explaining why this default was chosen.",
        ),
        "cite": _string(maxLength=120, description="NBC clause or bye-law table, if any."),
    },
}

#: A requested room. Sizes stay optional — the solver derives them from NBC minimums and
#: benchmarks, and an LLM guessing areas would be geometry-by-LLM through the back door.
ROOM_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "count"],
    "properties": {
        "type": {
            "enum": list(ROOM_TYPE_ENUM),
            "description": "Room type — the model's RoomType enum, generated from "
            "packages/model/schema/common.schema.json.",
        },
        "count": {"type": "integer", "minimum": 0, "maximum": 20},
        "notes": _string(maxLength=200),
        "targetAreaMm2": {
            "type": "integer",
            "minimum": 0,
            "description": "Only when the user stated a size. Integer mm^2.",
        },
    },
}

BRIEF_PARSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["brief", "assumptions"],
    "properties": {
        "brief": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "storeys": {"type": "integer", "minimum": 1, "maximum": 6},
                "hasStilt": {"type": "boolean"},
                "hasBasement": {"type": "boolean"},
                "rooms": {"type": "array", "maxItems": 40, "items": ROOM_REQUEST_SCHEMA},
                "vastuMode": {"enum": list(VASTU_MODE_ENUM)},
                "budgetInr": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Whole rupees. No decimals anywhere in this system.",
                },
                "parkingCount": {"type": "integer", "minimum": 0, "maximum": 20},
                "familySize": {"type": "integer", "minimum": 0, "maximum": 30},
                "adjacencies": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["a", "b", "strength"],
                        "properties": {
                            "a": _string(minLength=1, maxLength=40),
                            "b": _string(minLength=1, maxLength=40),
                            "strength": {"enum": ["required", "preferred", "avoid"]},
                        },
                    },
                },
                "notes": _string(maxLength=1000),
            },
        },
        "assumptions": {
            "type": "array",
            "maxItems": 40,
            "items": ASSUMPTION_SCHEMA,
            "description": "EVERY value not explicitly stated by the user.",
        },
        "stated": {
            "type": "array",
            "maxItems": 40,
            "items": _string(minLength=1, maxLength=120),
            "description": (
                "Dotted paths the brief text stated outright, e.g. brief.familySize. "
                "Together with `assumptions` this must account for every field you "
                "filled in — the code partitions on it, and anything in neither list "
                "is turned into an assumption chip automatically."
            ),
        },
        "unclear": {
            "type": "array",
            "maxItems": 10,
            "items": _string(minLength=1, maxLength=200),
            "description": "Things worth asking the architect about. Not blocking.",
        },
    },
}


# ---------------------------------------------------------------------------
# Copilot (§10) — command → {intent, ops[], needsClarification?, cannotDo?}
# ---------------------------------------------------------------------------
#: Loose on purpose — the real check is against ops.schema.json. See module docstring.
_CANDIDATE_OP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type", "payload"],
    "properties": {
        "type": _string(minLength=1, maxLength=64),
        "payload": {"type": "object"},
    },
}

COPILOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "ops"],
    "properties": {
        "intent": _string(
            minLength=1,
            maxLength=300,
            description="One plain sentence restating what you are about to do.",
        ),
        "ops": {
            "type": "array",
            "maxItems": MAX_COPILOT_OPS,
            "items": _CANDIDATE_OP_SCHEMA,
            "description": "Empty when you set needsClarification or cannotDo.",
        },
        "needsClarification": _string(
            minLength=1,
            maxLength=300,
            description="Ask ONE question when the command is ambiguous.",
        ),
        "cannotDo": _string(
            minLength=1,
            maxLength=300,
            description="Set when the request cannot be expressed as ops. Never guess.",
        ),
    },
}


# ---------------------------------------------------------------------------
# Rationale (§10) — solver facts → 60-word paragraph, list-then-write
# ---------------------------------------------------------------------------
RATIONALE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["factsUsed", "paragraph"],
    "properties": {
        "factsUsed": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": _string(minLength=1, maxLength=200),
            "description": (
                "Step 1 of the list-then-write pattern: copy VERBATIM the facts you "
                "will use. Anything not in the supplied facts is forbidden here, which "
                "makes an invented fact visible before it reaches the paragraph."
            ),
        },
        "paragraph": _string(
            minLength=1,
            maxLength=800,
            description="Step 2: at most %d words, using only factsUsed." % RATIONALE_WORD_LIMIT,
        ),
    },
}


SCHEMAS_BY_TASK: dict[str, dict[str, Any]] = {
    "brief.parse": BRIEF_PARSE_SCHEMA,
    "copilot.ops": COPILOT_SCHEMA,
    "rationale.write": RATIONALE_SCHEMA,
}


__all__ = [
    "ASSUMPTION_SCHEMA",
    "BRIEF_PARSE_SCHEMA",
    "COPILOT_SCHEMA",
    "MAX_COPILOT_OPS",
    "RATIONALE_SCHEMA",
    "RATIONALE_WORD_LIMIT",
    "ROOM_REQUEST_SCHEMA",
    "ROOM_TYPE_ENUM",
    "SCHEMAS_BY_TASK",
    "VASTU_MODE_ENUM",
]
