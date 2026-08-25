"""ids.py — element identity. Mirror of ``packages/model/src/ids.ts``.

Every element id is ``{type}_{ulid}`` — e.g. ``wall_01J9Z8QK7X3B2M4N5P6R7S8T9V``.
The prefix makes logs, op payloads and LLM prompts self-describing (a copilot
that puts ``room_...`` in a payload slot typed ``wallId`` is obviously wrong), and
the ULID makes ids sortable by creation time and collision-free without a central
sequence.

TWO KINDS OF ID:

- :func:`new_id` — random ULID, for elements a HUMAN or the SOLVER creates. MUST
  be called by the op *producer*, never inside :func:`garh_model.fold.fold`:
  creation ops carry their id in the payload so that ``replay(ops)`` is
  deterministic.
- :func:`derived_id` — deterministic id from a key string, for elements the model
  DERIVES (rooms from planar subdivision, slabs per storey). Derived elements
  must get the same id on every replay of the same op log, so a random ULID is
  not allowed here.

ULID GENERATION: the TypeScript package depends on ``ulid`` (MIT). Here the
48-bit-timestamp + 80-random-bits layout is implemented locally in a dozen lines
rather than adding a runtime dependency to ``garh_model`` — the format is a
public spec, ``new_id`` is never on a determinism-critical path, and keeping the
model core dependency-free means the solver and drawings workers can import it
without pulling anything in.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Set

from .sha256 import sha256_utf8

__all__ = [
    "CROCKFORD32",
    "ELEMENT_TYPES",
    "ID_PATTERN",
    "ULID_FIRST_CHARS",
    "IdError",
    "ParsedId",
    "UlidFactory",
    "set_ulid_factory",
    "seeded_ulid_factory",
    "new_id",
    "derived_id",
    "derived_id_unique",
    "try_parse_id",
    "parse_id",
    "is_id",
    "is_id_of",
    "id_type",
    "assert_id_of",
    "compare_ids",
]

#: Crockford base32 — ULID's alphabet (no I, L, O, U).
CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Every element family that owns an id namespace. Order matches ``ids.ts``.
#: NOTE the facade component namespace is ``facadecomp``, not ``facadeComponent``.
ELEMENT_TYPES: Sequence[str] = (
    "storey",
    "wall",
    "opening",
    "room",
    "stair",
    "slab",
    "column",
    "furniture",
    "balcony",
    "facade",
    "facadecomp",
    "material",
    "annotation",
    "sheet",
    "group",
    "op",
    "plot",
    "brief",
    "version",
    "job",
)

_ELEMENT_TYPE_SET = frozenset(ELEMENT_TYPES)

#: ``type_ULID`` — 26 Crockford base32 chars, uppercase.
ID_PATTERN = re.compile(r"^([a-z][a-z0-9]{1,15})_([0-9ABCDEFGHJKMNPQRSTVWXYZ]{26})$")

#: Max ULID timestamp char: the first char of a valid ULID is 0-7.
ULID_FIRST_CHARS = "01234567"


class IdError(ValueError):
    """Raised when an id is malformed. Mirrors the TypeScript ``IdError``."""

    code = "ID_INVALID"


# ---------------------------------------------------------------------------
# Random ids
# ---------------------------------------------------------------------------

#: A ULID factory. Injectable so tests can make id generation deterministic.
UlidFactory = Callable[[], str]

_ulid_factory: Optional[UlidFactory] = None


def set_ulid_factory(factory: Optional[UlidFactory]) -> None:
    """Install a ULID factory (tests, or a seeded solver run). ``None`` restores
    the random default."""
    global _ulid_factory
    _ulid_factory = factory


def _encode_crockford(value: int, length: int) -> str:
    """Encode ``value`` as ``length`` Crockford base32 chars, most significant first.

    Mirrors ``encodeCrockford`` in ``ids.ts``, including its truncation behaviour
    when the value needs more than ``length`` characters.
    """
    n = abs(int(value))
    out = ""
    for _ in range(length):
        out = CROCKFORD32[n % 32] + out
        n //= 32
    return out


def seeded_ulid_factory(seed: int = 1) -> UlidFactory:
    """Deterministic, monotonic ULID factory for tests and golden fixtures.

    ``seed`` fixes the 80 random bits; the 48-bit timestamp is a counter. The
    arithmetic is identical to the TypeScript mirror (both stay well inside the
    53-bit exactly-representable range, so the JS float maths and the Python int
    maths agree).
    """
    counter = 0

    def factory() -> str:
        nonlocal counter
        counter += 1
        time_part = _encode_crockford(counter, 10)
        rand_part = _encode_crockford(seed * 0x9E3779B1 + counter * 0x85EBCA6B, 16)
        return time_part + rand_part

    return factory


def _default_ulid() -> str:
    """A spec-shaped ULID: 48-bit millisecond timestamp + 80 random bits."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.randbits(80)
    return _encode_crockford(ms, 10) + _encode_crockford(rand, 16)


def new_id(element_type: str) -> str:
    """Fresh id for a NEW element.

    Never call this inside ``fold()`` — creation ops carry their id in the
    payload, which is what makes ``replay`` deterministic.
    """
    if element_type not in _ELEMENT_TYPE_SET:
        raise IdError(f"Unknown element type {element_type!r}")
    factory = _ulid_factory
    ulid_value = factory() if factory is not None else _default_ulid()
    return f"{element_type}_{ulid_value}"


# ---------------------------------------------------------------------------
# Derived (deterministic) ids
# ---------------------------------------------------------------------------


def derived_id(element_type: str, key: str) -> str:
    """Deterministic id from a key string: ``type_<130 bits of sha256(key)>``.

    CROSS-LANGUAGE CONTRACT (room ids appear in the state hash, so this must be
    byte-identical to ``derivedId`` in ``ids.ts``), exactly:

    1. ``digest = sha256(utf8(key))``
    2. take the FIRST 130 bits of the digest, most-significant bit first
    3. set bit 0 and bit 1 to zero (forces the leading base32 char into 0-7, so
       the value stays a *syntactically valid* ULID and strict ULID parsers in
       other tools do not choke on it)
    4. emit 26 characters, 5 bits each, MSB-first, through :data:`CROCKFORD32`
    """
    hex_digest = sha256_utf8(key)
    bits = []
    for i in range(17):  # 17 bytes = 136 bits, we keep the first 130
        byte = int(hex_digest[i * 2 : i * 2 + 2], 16)
        for b in range(7, -1, -1):
            bits.append((byte >> b) & 1)
    bits = bits[:130]
    bits[0] = 0
    bits[1] = 0
    out = ""
    for i in range(26):
        v = 0
        for b in range(5):
            v = v * 2 + bits[i * 5 + b]
        out += CROCKFORD32[v]
    return f"{element_type}_{out}"


def derived_id_unique(element_type: str, key: str, taken: Set[str]) -> str:
    """Derived id with a collision escape hatch.

    ``taken`` lets the caller keep ids unique when two derived elements hash to
    the same key (only reachable when two elements really are geometrically
    identical).
    """
    candidate = derived_id(element_type, key)
    salt = 0
    while candidate in taken:
        salt += 1
        candidate = derived_id(element_type, f"{key}#{salt}")
    return candidate


# ---------------------------------------------------------------------------
# Parse / validate / guards
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedId:
    """A successfully parsed element id."""

    type: str
    ulid: str
    raw: str


def try_parse_id(value: object) -> Optional[ParsedId]:
    """Parse an id, or ``None`` if it is not well-formed / not a known type."""
    if not isinstance(value, str):
        return None
    m = ID_PATTERN.match(value)
    if m is None:
        return None
    element_type = m.group(1)
    ulid_part = m.group(2)
    if element_type not in _ELEMENT_TYPE_SET:
        return None
    if ulid_part[0] not in ULID_FIRST_CHARS:
        return None
    return ParsedId(type=element_type, ulid=ulid_part, raw=value)


def parse_id(value: object) -> ParsedId:
    """Parse an id or raise :class:`IdError`."""
    parsed = try_parse_id(value)
    if parsed is None:
        raise IdError(f"Not a Garh element id: {value!r}")
    return parsed


def is_id(value: object) -> bool:
    """True for any valid element id."""
    return try_parse_id(value) is not None


def is_id_of(element_type: str, value: object) -> bool:
    """True for a valid element id of exactly this type."""
    parsed = try_parse_id(value)
    return parsed is not None and parsed.type == element_type


def id_type(value: object) -> Optional[str]:
    """``id_type('wall_01J...') == 'wall'``, else ``None``."""
    parsed = try_parse_id(value)
    return None if parsed is None else parsed.type


def assert_id_of(element_type: str, value: object, field: str) -> str:
    """Assert an id of a given type, returning it."""
    if not is_id_of(element_type, value):
        raise IdError(
            f"{field} must be a {element_type} id ({element_type}_<ulid>), got {value!r}"
        )
    return str(value)


def compare_ids(a: str, b: str) -> int:
    """Sort ids stably: plain byte/code-point comparison.

    Element ids are ASCII, so this agrees with the TypeScript ``compareIds``
    (which compares UTF-16 code units) on every value the model can hold.
    """
    if a < b:
        return -1
    if a > b:
        return 1
    return 0
